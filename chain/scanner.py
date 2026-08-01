"""New-token launch scanner (SPEC-v2 §6).

Detects incubation-token launches by polling blocks for:
  (a) curve ``Buy`` events emitted by the configured nad.fun-style
      lens/factory (``Config.NAD_FUN_LENS``), and/or
  (b) ``PairCreated`` events from configured UniV2 factories
      (``Config.PAIR_FACTORIES``, comma-separated).

Deduplicates per token address and forwards ``NewTokenEvent`` to the
``on_new_token`` callback. If no reliable on-chain source is configured,
it logs and keeps idling — it never invents events and never raises.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from web3 import Web3

from core.models import NewTokenEvent, TokenInfo

from chain.abis import CURVE_BUY_TOPIC, WMON_ADDRESS, event_topic
from chain.client import ensure_connected, get_w3
from chain.detector import _get, _hex, _topic_address

logger = logging.getLogger(__name__)

PAIR_CREATED_SIG = "PairCreated(address,address,address,uint256)"
PAIR_CREATED_TOPIC = event_topic(PAIR_CREATED_SIG)

_MAX_BACKOFF = 60.0
_MAX_SEEN = 50_000


class NewTokenScanner:
    def __init__(self, on_new_token) -> None:
        """on_new_token: async callable (event: NewTokenEvent) -> None"""
        self._on_new_token = on_new_token
        self._running = False
        self._w3 = None
        self._last_block: Optional[int] = None
        self._seen_tokens: set[str] = set()
        self._interval: Optional[float] = None  # test/override hook
        self._warned_no_source = False

    # -- main loop ----------------------------------------------------------

    async def start(self) -> None:
        """Polling loop until ``stop()`` is called. Never raises."""
        from core.config import load_config

        cfg = load_config()
        if not cfg.SCANNER_ENABLED:
            logger.info("new-token scanner disabled (SCANNER_ENABLED=false)")
            return
        self._running = True
        interval = self._interval or float(cfg.POLL_INTERVAL)
        backoff = interval
        self._w3 = get_w3()

        while self._running:
            try:
                self._w3 = await ensure_connected(self._w3)
                await self._poll_once(cfg)
                backoff = interval
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - never crash the loop
                logger.warning("scanner poll error: %s (retry in %.1fs)", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF)

    async def stop(self) -> None:
        self._running = False

    # -- internals -----------------------------------------------------------

    def _sources(self, cfg) -> tuple[list[str], list[str]]:
        """(curve factories, pair factories) checksum-normalized."""
        curve_factories: list[str] = []
        pair_factories: list[str] = []
        raw_lens = (getattr(cfg, "NAD_FUN_LENS", "") or "").strip()
        if raw_lens:
            try:
                curve_factories.append(Web3.to_checksum_address(raw_lens))
            except Exception:  # noqa: BLE001
                logger.warning("invalid NAD_FUN_LENS address: %s", raw_lens)
        for raw in (getattr(cfg, "PAIR_FACTORIES", "") or "").split(","):
            raw = raw.strip()
            if not raw:
                continue
            try:
                pair_factories.append(Web3.to_checksum_address(raw))
            except Exception:  # noqa: BLE001
                logger.warning("invalid PAIR_FACTORIES entry: %s", raw)
        return curve_factories, pair_factories

    async def _poll_once(self, cfg) -> None:
        curve_factories, pair_factories = self._sources(cfg)
        if not curve_factories and not pair_factories:
            if not self._warned_no_source:
                self._warned_no_source = True
                logger.info(
                    "new-token scanner: no NAD_FUN_LENS or PAIR_FACTORIES "
                    "configured; no reliable launch source, idling"
                )
            return

        latest = int(await self._w3.eth.get_block_number())
        if self._last_block is None:
            # First run: start at the tip — do not rescan history for alerts.
            self._last_block = latest
            return
        from_block = self._last_block + 1
        if from_block > latest:
            return
        to_block = min(latest, from_block + max(1, int(cfg.BLOCKS_PER_POLL)) - 1)

        if curve_factories:
            logs = await self._w3.eth.get_logs(
                {
                    "fromBlock": from_block,
                    "toBlock": to_block,
                    "address": curve_factories,
                    "topics": [CURVE_BUY_TOPIC],
                }
            )
            for log in logs:
                await self._handle_curve_buy(log)

        if pair_factories:
            logs = await self._w3.eth.get_logs(
                {
                    "fromBlock": from_block,
                    "toBlock": to_block,
                    "address": pair_factories,
                    "topics": [PAIR_CREATED_TOPIC],
                }
            )
            for log in logs:
                await self._handle_pair_created(log)

        self._last_block = to_block

    def _mark_seen(self, token_address: str) -> bool:
        """True if the token is new (and records it)."""
        key = token_address.lower()
        if key in self._seen_tokens:
            return False
        self._seen_tokens.add(key)
        if len(self._seen_tokens) > _MAX_SEEN:
            self._seen_tokens = set(list(self._seen_tokens)[_MAX_SEEN // 2 :])
        return True

    async def _token_meta(self, token_address: str) -> TokenInfo:
        """Best-effort ERC20 metadata; safe fallbacks, never raises."""
        try:
            from chain.price import get_token_info

            return await get_token_info(token_address)
        except Exception as exc:  # noqa: BLE001
            logger.debug("get_token_info(%s) failed: %s", token_address, exc)
            return TokenInfo(
                address=token_address,
                name="",
                symbol="",
                decimals=18,
                total_supply=0.0,
                kind="curve",
            )

    async def _block_timestamp(self, block_number: int) -> int:
        import time

        try:
            block = await self._w3.eth.get_block(block_number)
            ts = _get(block, "timestamp")
            if ts is not None:
                return int(ts)
        except Exception:  # noqa: BLE001
            pass
        return int(time.time())

    async def _emit(self, token_address: str, source_address: str, log: Any) -> None:
        if not token_address or not self._mark_seen(token_address):
            return
        try:
            token_address = Web3.to_checksum_address(token_address)
        except Exception:  # noqa: BLE001
            return
        info = await self._token_meta(token_address)
        block_number = int(_get(log, "blockNumber", _get(log, "block_number", 0)) or 0)
        event = NewTokenEvent(
            token_address=token_address,
            token_symbol=info.symbol or "?",
            token_name=info.name or token_address,
            curve_address=str(source_address),
            tx_hash=_hex(_get(log, "transactionHash")),
            block_number=block_number,
            timestamp=await self._block_timestamp(block_number),
        )
        try:
            await self._on_new_token(event)
        except Exception as exc:  # noqa: BLE001
            logger.warning("on_new_token callback failed: %s", exc)

    async def _handle_curve_buy(self, log: Any) -> None:
        """Curve Buy from a configured lens/factory: indexed token = topic 2."""
        try:
            topics = _get(log, "topics") or []
            if len(topics) < 3:
                return
            token = _topic_address(topics[2])
            emitter = str(_get(log, "address", "") or "")
            await self._emit(token, emitter, log)
        except Exception as exc:  # noqa: BLE001
            logger.warning("curve-buy handling failed: %s", exc)

    async def _handle_pair_created(self, log: Any) -> None:
        """PairCreated from a configured factory: token0/topic1, token1/topic2."""
        try:
            topics = _get(log, "topics") or []
            if len(topics) < 3:
                return
            token0 = _topic_address(topics[1])
            token1 = _topic_address(topics[2])
            # Pick the non-WMON side as the launched token.
            wmon = WMON_ADDRESS.lower()
            if token0.lower() == wmon:
                token = token1
            elif token1.lower() == wmon:
                token = token0
            else:
                token = token1
            # Pair address: first 32-byte data word.
            data = _get(log, "data", b"") or b""
            raw = bytes(data) if isinstance(data, (bytes, bytearray)) else bytes.fromhex(
                str(data).removeprefix("0x")
            )
            pair = "0x" + raw[:32].hex()[-40:] if len(raw) >= 32 else ""
            await self._emit(token, pair or str(_get(log, "address", "") or ""), log)
        except Exception as exc:  # noqa: BLE001
            logger.warning("pair-created handling failed: %s", exc)
