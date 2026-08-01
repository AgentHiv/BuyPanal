"""MON -> USD price from the on-chain WMON/USDC pair (SPEC-v3 §3).

Reads ``getReserves()`` from the verified PancakeSwap v2-style WMON/USDC
pair on Monad mainnet (see ``chain.abis.WMON_USDC_PAIR`` for the on-chain
verification trail) and converts with the 18/6 decimal adjustment:

    price_usd = (reserve_usdc / 1e6) / (reserve_wmon / 1e18)

The result is cached in memory for ``CACHE_TTL`` seconds so periodic
callers do not hammer the RPC. The module NEVER raises and returns 0.0
("unknown") on any failure — callers must treat 0.0 as "no price feed".

Manual override: when ``Config.MON_USD_PRICE > 0`` the configured value
is returned directly and no on-chain call is made.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from chain.abis import (
    PAIR_OR_CURVE_ABI,
    USDC_ADDRESS,
    WMON_ADDRESS,
    WMON_USDC_PAIR,
)

logger = logging.getLogger(__name__)

CACHE_TTL = 60.0  # seconds (SPEC-v3 §3)

_cache_price: float = 0.0
_cache_ts: float = 0.0


def _clear_cache() -> None:
    """Reset the in-memory cache (test hook)."""
    global _cache_price, _cache_ts
    _cache_price = 0.0
    _cache_ts = 0.0


async def _fetch_pair_price(w3) -> float:
    """Read the WMON/USDC pair and compute the MON price in USD.

    Raises on failure — ``get_mon_usd_price`` is the never-raising wrapper.
    """
    from web3 import Web3

    pair = w3.eth.contract(
        address=Web3.to_checksum_address(WMON_USDC_PAIR),
        abi=PAIR_OR_CURVE_ABI,
    )
    reserves = await pair.functions.getReserves().call()
    reserve0, reserve1 = int(reserves[0]), int(reserves[1])
    if reserve0 <= 0 or reserve1 <= 0:
        raise ValueError("empty reserves")

    # Identify which side is WMON (18 dec) and which is USDC (6 dec).
    token0 = str(await pair.functions.token0().call()).lower()
    token1 = str(await pair.functions.token1().call()).lower()
    wmon, usdc = WMON_ADDRESS.lower(), USDC_ADDRESS.lower()
    if token0 == wmon and token1 == usdc:
        reserve_wmon, reserve_usdc = reserve0, reserve1
    elif token0 == usdc and token1 == wmon:
        reserve_wmon, reserve_usdc = reserve1, reserve0
    else:
        raise ValueError(f"unexpected pair tokens: {token0} / {token1}")

    price = (reserve_usdc / 1e6) / (reserve_wmon / 1e18)
    if price <= 0:
        raise ValueError("non-positive price")
    return float(price)


async def _discover_pair_via_sync_logs(w3) -> Optional[str]:
    """Fallback: find a live WMON/USDC pool by scanning recent Sync logs.

    Looks at UniV2-style ``Sync(uint112,uint112)`` logs from the last blocks
    and returns the emitter whose token0/token1 are WMON and USDC. Returns
    None when nothing matches. Never raises.
    """
    try:
        from web3 import Web3

        from chain.abis import UNIV2_PAIR_ABI, event_topic

        latest = int(await w3.eth.get_block_number())
        topic = event_topic("Sync(uint112,uint112)")
        wmon, usdc = WMON_ADDRESS.lower(), USDC_ADDRESS.lower()
        # Scan a handful of recent windows (public RPC rejects big ranges).
        end = latest
        for _ in range(20):
            start = max(0, end - 49)
            try:
                logs = await w3.eth.get_logs(
                    {"fromBlock": start, "toBlock": end, "topics": [topic]}
                )
            except Exception:  # noqa: BLE001
                return None
            for log in reversed(logs):
                address = log.get("address") if isinstance(log, dict) else log["address"]
                try:
                    pair = w3.eth.contract(
                        address=Web3.to_checksum_address(str(address)),
                        abi=UNIV2_PAIR_ABI,
                    )
                    token0 = str(await pair.functions.token0().call()).lower()
                    token1 = str(await pair.functions.token1().call()).lower()
                except Exception:  # noqa: BLE001
                    continue
                if {token0, token1} == {wmon, usdc}:
                    return str(address)
            end = start - 1
            if end <= 0:
                break
    except Exception:  # noqa: BLE001 - best effort fallback
        logger.debug("Sync-log pair discovery failed", exc_info=True)
    return None


async def get_mon_usd_price(w3=None) -> float:
    """Price of MON in USD from the WMON/USDC pair on Monad.

    - Returns the manual override (``Config.MON_USD_PRICE``) when > 0 and
      makes no on-chain call in that case.
    - Otherwise reads ``getReserves`` from the verified pair (60s cache).
    - Returns 0.0 on any failure. Never raises.
    """
    global _cache_price, _cache_ts
    try:
        from core.config import load_config

        override = float(getattr(load_config(), "MON_USD_PRICE", 0.0) or 0.0)
    except Exception:  # noqa: BLE001
        override = 0.0
    if override > 0:
        return override

    now = time.monotonic()
    if _cache_price > 0 and (now - _cache_ts) < CACHE_TTL:
        return _cache_price

    try:
        if w3 is None:
            from chain.client import get_w3

            w3 = get_w3()
        price = await _fetch_pair_price(w3)
    except Exception as exc:  # noqa: BLE001
        logger.warning("WMON/USDC price fetch failed: %s", exc)
        # Fallback: try to discover the pair from recent Sync logs, in case
        # the hardcoded address ever goes stale.
        try:
            discovered = await _discover_pair_via_sync_logs(w3) if w3 is not None else None
            if discovered:
                from web3 import Web3

                pair = w3.eth.contract(
                    address=Web3.to_checksum_address(discovered),
                    abi=PAIR_OR_CURVE_ABI,
                )
                reserves = await pair.functions.getReserves().call()
                token0 = str(await pair.functions.token0().call()).lower()
                reserve0, reserve1 = int(reserves[0]), int(reserves[1])
                if token0 == WMON_ADDRESS.lower():
                    reserve_wmon, reserve_usdc = reserve0, reserve1
                else:
                    reserve_wmon, reserve_usdc = reserve1, reserve0
                price = (reserve_usdc / 1e6) / (reserve_wmon / 1e18)
                if price <= 0:
                    raise ValueError("non-positive price")
                logger.info("MON/USD price from discovered pair %s", discovered)
            else:
                return 0.0
        except Exception as exc2:  # noqa: BLE001
            logger.warning("MON/USD discovery fallback failed: %s", exc2)
            return 0.0

    _cache_price = float(price)
    _cache_ts = now
    return _cache_price
