"""Price alert monitor (SPEC-v2 §5).

Polls the active price alerts (via an injected provider, normally
``Database.all_active_price_alerts``) every ``POLL_INTERVAL * 5`` seconds,
fetches the current price in MON via ``chain.price.get_price_mon`` and
fires ``on_alert`` when an alert's target is crossed.

Each alert fires at most once per monitor instance — the bot layer is
expected to deactivate the alert in the db when ``on_alert`` runs.
This module never raises.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

from core.models import PriceAlert

from chain.price import get_price_mon

logger = logging.getLogger(__name__)

_MAX_BACKOFF = 60.0


class PriceMonitor:
    def __init__(self, on_alert) -> None:
        """on_alert: async callable (alert: PriceAlert, current_price_mon: float)"""
        self._on_alert = on_alert
        self._alert_provider: Optional[Callable[[], list[PriceAlert]]] = None
        self._running = False
        self._interval: Optional[float] = None  # test/override hook
        self._fired: set = set()  # alert ids (or fallback keys) already fired

    def set_alert_provider(self, fn) -> None:
        """fn() -> list[PriceAlert] of active alerts (e.g. from the db)."""
        self._alert_provider = fn

    # -- main loop ---------------------------------------------------------

    async def start(self) -> None:
        """Polling loop until ``stop()`` is called. Never raises."""
        from core.config import load_config

        cfg = load_config()
        interval = self._interval
        if interval is None or interval <= 0:
            interval = max(1.0, float(cfg.POLL_INTERVAL) * 5)
        self._running = True
        backoff = interval

        while self._running:
            try:
                await self._check_once()
                backoff = interval
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - never crash the loop
                logger.warning("price monitor error: %s (retry in %.1fs)", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF)

    async def stop(self) -> None:
        self._running = False

    # -- internals ----------------------------------------------------------

    @staticmethod
    def _alert_key(alert: PriceAlert):
        if alert.id is not None:
            return ("id", int(alert.id))
        return (
            "anon",
            alert.chat_id,
            alert.token_address.lower(),
            alert.direction,
            float(alert.target_mon),
        )

    async def _check_once(self) -> None:
        """Single pass over all active alerts. Never raises."""
        if self._alert_provider is None:
            return
        try:
            alerts = list(self._alert_provider() or [])
        except Exception as exc:  # noqa: BLE001
            logger.warning("alert provider failed: %s", exc)
            return
        if not alerts:
            return

        # Fetch the price once per token to avoid hammering the RPC.
        price_cache: dict[str, float] = {}
        for alert in alerts:
            if not getattr(alert, "active", True):
                continue
            key = self._alert_key(alert)
            if key in self._fired:
                continue
            token = str(alert.token_address).lower()
            if token not in price_cache:
                try:
                    price_cache[token] = float(await get_price_mon(alert.token_address))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("get_price_mon(%s) failed: %s", token, exc)
                    price_cache[token] = 0.0
            price = price_cache[token]
            if price <= 0.0:
                continue  # unknown price: never trigger (avoids false "below")

            direction = str(alert.direction).lower()
            triggered = (direction == "above" and price >= alert.target_mon) or (
                direction == "below" and price <= alert.target_mon
            )
            if not triggered:
                continue

            self._fired.add(key)  # fire at most once per monitor instance
            try:
                await self._on_alert(alert, price)
            except Exception as exc:  # noqa: BLE001
                logger.warning("on_alert callback failed for alert %s: %s", alert.id, exc)
