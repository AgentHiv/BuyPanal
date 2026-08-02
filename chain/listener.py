"""Block-polling buy listener.

Polls ``eth_getLogs`` for ERC20 Transfer events of all tracked token
addresses (batched in a single filter), dedupes by (tx_hash, logIndex),
classifies candidates via ``chain.detector.build_buy_event`` and forwards
confirmed buys to the ``on_buy`` callback. RPC errors trigger exponential
backoff; the loop never crashes.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Optional

from web3 import Web3

from chain.abis import TRANSFER_TOPIC
from chain.client import ensure_connected, get_w3
from chain.detector import _get, _hex
from chain.detector import build_buy_event, build_sell_event
from chain.price import get_token_info

logger = logging.getLogger(__name__)

_MAX_BACKOFF = 30.0
_MAX_SEEN = 10_000  # dedup cache size cap


class BuyListener:
    def __init__(self, on_buy) -> None:
        """on_buy: async callable (chat_ids: list[int], buy: BuyEvent) -> None"""
        self._on_buy = on_buy
        self._on_sell = None  # async callable (chat_ids: list[int], sell: SellEvent)
        self._tokens: set[str] = set()
        self._token_info: dict[str, Any] = {}
        self._chat_resolver: Optional[Callable[[str], list[int]]] = None
        self._seen: set[tuple[str, int]] = set()
        self._running = False
        self._w3 = None
        self._last_block: Optional[int] = None

    # -- configuration ----------------------------------------------------

    def set_chat_resolver(self, fn: Callable[[str], list[int]]) -> None:
        """Set fn(address) -> list[int] mapping a token to subscribed chats."""
        self._chat_resolver = fn

    def set_sell_callback(self, fn) -> None:
        """Set fn(chat_ids: list[int], sell: SellEvent) for sell alerts.

        The listener emits sells detected through the same Transfer-log
        channel as buys (SPEC-v2 §9 listener contract).
        """
        self._on_sell = fn

    async def add_token(self, address: str) -> None:
        self._tokens.add(Web3.to_checksum_address(address))

    async def remove_token(self, address: str) -> None:
        self._tokens.discard(Web3.to_checksum_address(address))
        self._token_info.pop(Web3.to_checksum_address(address), None)

    # -- main loop --------------------------------------------------------

    async def start(self) -> None:
        """Infinite polling loop until ``stop()`` is called."""
        from core.config import load_config

        cfg = load_config()
        self._running = True
        backoff = cfg.POLL_INTERVAL
        self._w3 = get_w3()

        while self._running:
            try:
                self._w3 = await ensure_connected(self._w3)
                await self._poll_once(cfg.BLOCKS_PER_POLL)
                backoff = cfg.POLL_INTERVAL
                await asyncio.sleep(cfg.POLL_INTERVAL)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - never crash the loop
                logger.warning("listener poll error: %s (retry in %.1fs)", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF)

    async def stop(self) -> None:
        self._running = False

    # -- internals ---------------------------------------------------------

    async def _poll_once(self, blocks_per_poll: int) -> None:
        latest = int(await self._w3.eth.get_block_number())
        if self._last_block is None:
            # First run: start at the tip, do not rescan history for alerts.
            self._last_block = latest
            return
        from_block = self._last_block + 1
        if from_block > latest:
            return
        to_block = min(latest, from_block + max(1, blocks_per_poll) - 1)

        if self._tokens:
            logs = await self._w3.eth.get_logs(
                {
                    "fromBlock": from_block,
                    "toBlock": to_block,
                    "address": sorted(self._tokens),
                    "topics": [TRANSFER_TOPIC],
                }
            )
            for log in logs:
                await self._handle_transfer(log)

        self._last_block = to_block

    async def _handle_transfer(self, log: Any) -> None:
        tx_hash = _hex(_get(log, "transactionHash"))
        log_index = int(_get(log, "logIndex", 0) or 0)
        key = (tx_hash, log_index)
        if key in self._seen:
            return
        self._seen.add(key)
        if len(self._seen) > _MAX_SEEN:
            # Drop an arbitrary half of the cache to bound memory.
            self._seen = set(list(self._seen)[_MAX_SEEN // 2 :])

        address = str(_get(log, "address", "") or "")
        try:
            address = Web3.to_checksum_address(address)
        except Exception:  # noqa: BLE001
            return

        token_info = self._token_info.get(address)
        if token_info is None:
            try:
                token_info = await get_token_info(address)
            except Exception as exc:  # noqa: BLE001
                logger.warning("get_token_info(%s) failed: %s", address, exc)
                return
            self._token_info[address] = token_info

        buy = await build_buy_event(self._w3, log, token_info)
        if buy is None:
            if self._on_sell is not None:
                await self._handle_sell(log, token_info, address)
            return

        chat_ids = self._resolve_chats(address)
        if not chat_ids:
            return

        await self._on_buy(chat_ids, buy)

    def _resolve_chats(self, address: str) -> list[int]:
        chat_ids: list[int] = []
        if self._chat_resolver is not None:
            try:
                # Resolvers may key by lowercase (db storage) or checksum;
                # try the address as-is first, then normalized.
                chat_ids = list(self._chat_resolver(address) or [])
                if not chat_ids:
                    chat_ids = list(self._chat_resolver(str(address).lower()) or [])
            except Exception as exc:  # noqa: BLE001
                logger.warning("chat resolver failed for %s: %s", address, exc)
        return chat_ids

    async def _handle_sell(self, log: Any, token_info: Any, address: str) -> None:
        try:
            sell = await build_sell_event(self._w3, log, token_info)
        except Exception as exc:  # noqa: BLE001 - sell path must never break buys
            logger.warning("build_sell_event crashed for %s: %s", address, exc)
            return
        if sell is None:
            return

        chat_ids = self._resolve_chats(address)
        if not chat_ids:
            return

        try:
            await self._on_sell(chat_ids, sell)
        except Exception as exc:  # noqa: BLE001
            logger.warning("sell callback failed for %s: %s", address, exc)
