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
        self._price_provider: Optional[Callable[[], float]] = None  # SPEC-v3
        self._running = False
        self._interval: Optional[float] = None  # test/override hook
        self._fired: set = set()  # alert ids (or fallback keys) already fired

    def set_alert_provider(self, fn) -> None:
        """fn() -> list[PriceAlert] of active alerts (e.g. from the db)."""
        self._alert_provider = fn

    def set_price_provider(self, fn) -> None:
        """fn() -> float: current MON price in USD (SPEC-v3 §4).

        Used to evaluate ``currency == "USD"`` alerts. When unset (or the
        provider returns <= 0) USD alerts simply do not fire.
        """
        self._price_provider = fn

    def _mon_usd(self) -> float:
        """Current MON->USD rate from the injected provider (0.0 if none)."""
        if self._price_provider is None:
            return 0.0
        try:
            value = float(self._price_provider() or 0.0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("mon price provider failed: %s", exc)
            return 0.0
        return max(0.0, value)

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
        mon_usd: Optional[float] = None  # lazy: only needed for USD alerts
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

            # SPEC-v3 §4: USD alerts compare price_mon * mon_usd vs target_usd;
            # MON alerts keep the v2 behaviour (compare vs target_mon).
            currency = str(getattr(alert, "currency", "MON") or "MON").upper()
            direction = str(alert.direction).lower()
            if currency == "USD":
                target_usd = getattr(alert, "target_usd", None)
                if target_usd is None or float(target_usd) <= 0:
                    continue
                if mon_usd is None:
                    mon_usd = self._mon_usd()
                    if mon_usd <= 0.0:
                        logger.info(
                            "no MON/USD price feed: USD price alerts paused"
                        )
                if mon_usd <= 0.0:
                    continue  # USD alerts never fire without a price feed
                compare_value = price * mon_usd
                target = float(target_usd)
            else:
                compare_value = price
                target = float(alert.target_mon)

            triggered = (direction == "above" and compare_value >= target) or (
                direction == "below" and compare_value <= target
            )
            if not triggered:
                continue

            self._fired.add(key)  # fire at most once per monitor instance
            try:
                await self._on_alert(alert, price)
            except Exception as exc:  # noqa: BLE001
                logger.warning("on_alert callback failed for alert %s: %s", alert.id, exc)
