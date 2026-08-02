"""Bonding-curve (incubation) status for nad.fun style tokens.

Detects whether a token still trades on its bonding curve by looking for
recent curve ``Buy`` events indexed by token address, then reads the
curve reserves to estimate progress toward graduation. All failures are
swallowed: unknown values are None and this module never raises.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from web3 import Web3

from core.models import CurveInfo

from chain.abis import CURVE_ABI, CURVE_BUY_TOPIC
from chain.client import get_w3
from chain.detector import _get

logger = logging.getLogger(__name__)

# nad.fun-style graduation target: ~225,000 MON raised.
GRADUATION_MON = 225_000.0
# How far back to scan for curve Buy events.
_CURVE_SCAN_BLOCKS = 50_000


def _unknown(token_address: str) -> CurveInfo:
    return CurveInfo(
        token_address=token_address,
        is_incubating=False,
        progress_pct=None,
        mon_raised=None,
        graduated=False,
        curve_address=None,
    )


async def _find_curve_address(w3: Any, token_address: str) -> Optional[str]:
    """Address of the most recent curve emitting Buy events for the token.

    Scans newest-first in block windows (public RPCs reject large ranges)
    and returns as soon as a curve Buy event is found. The token is an
    indexed arg whose position varies by deployment — real nad.fun curves
    put it in topic1, others in topic2 — so both layouts are scanned.
    """
    try:
        from chain.client import iter_logs_windowed

        latest = int(await w3.eth.get_block_number())
        from_block = max(0, latest - _CURVE_SCAN_BLOCKS)
        token_topic = "0x" + "0" * 24 + token_address.lower().removeprefix("0x")
        for topics in ([CURVE_BUY_TOPIC, token_topic], [CURVE_BUY_TOPIC, None, token_topic]):
            async for logs in iter_logs_windowed(
                w3,
                {"topics": topics},
                from_block,
                latest,
                max_windows=200,
            ):
                if logs:
                    return Web3.to_checksum_address(str(_get(logs[-1], "address")))
        return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("curve scan failed for %s: %s", token_address, exc)
        return None


async def _latest_sync_reserves(
    w3: Any, curve_address: str, token_address: str
) -> Optional[tuple[float, float]]:
    """(mon_reserve, token_reserve) from the curve's latest Sync event.

    nad.fun curve clones expose no getReserves()/reserves() getters, so the
    real reserves are decoded from ``Sync(address,uint256,uint256,uint256,
    uint256)``: word1 = real token reserve, word2 = real MON reserve (the
    other two words are the virtual offsets; real reserves are what the
    curve prices at — verified against on-chain trade execution prices).
    """
    try:
        from chain.abis import CURVE_SYNC_FULL_TOPIC
        from chain.client import iter_logs_windowed

        latest = int(await w3.eth.get_block_number())
        token_topic = "0x" + "0" * 24 + token_address.lower().removeprefix("0x")
        params = {"address": curve_address, "topics": [CURVE_SYNC_FULL_TOPIC, token_topic]}
        async for logs in iter_logs_windowed(w3, params, 0, latest, max_windows=200):
            if not logs:
                continue
            data = _get(logs[-1], "data", b"") or b""
            raw = bytes(data) if isinstance(data, (bytes, bytearray)) else bytes.fromhex(str(data).removeprefix("0x"))
            words = [int.from_bytes(raw[i : i + 32], "big") for i in range(0, len(raw), 32)]
            if len(words) >= 3:
                return words[2] / 1e18, words[1] / 1e18
        return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("curve Sync scan failed for %s: %s", token_address, exc)
        return None


async def _curve_reserves(
    w3: Any, curve_address: str, token_address: Optional[str] = None
) -> Optional[tuple[float, float]]:
    """(mon_reserve, token_reserve) in human units, or None."""
    curve = w3.eth.contract(address=curve_address, abi=CURVE_ABI)
    try:  # getReserves() UniV2-style
        reserves = await curve.functions.getReserves().call()
        r0, r1 = float(reserves[0]), float(reserves[1])
        if r0 > 0 or r1 > 0:
            return r0 / 1e18, r1 / 1e18
    except Exception:  # noqa: BLE001
        pass
    try:  # reserves() (reserveMon, reserveToken)
        reserves = await curve.functions.reserves().call()
        r_mon, r_token = float(reserves[0]), float(reserves[1])
        if r_mon > 0 or r_token > 0:
            return r_mon / 1e18, r_token / 1e18
    except Exception:  # noqa: BLE001
        pass
    if token_address:  # nad.fun clones: decode the latest Sync event
        return await _latest_sync_reserves(w3, curve_address, token_address)
    return None


async def get_curve_info(token_address: str) -> CurveInfo:
    """Bonding-curve status for a token. Never raises.

    is_incubating=True while trading on the curve; graduated=True once the
    curve's token reserve is depleted (liquidity migrated to a DEX).
    Unknown values are None.
    """
    try:
        token_address = Web3.to_checksum_address(token_address)
    except Exception:  # noqa: BLE001
        return _unknown(str(token_address))

    try:
        w3 = get_w3()
        curve_address = await _find_curve_address(w3, token_address)
        if curve_address is None:
            return _unknown(token_address)

        reserves = await _curve_reserves(w3, curve_address, token_address)
        mon_raised: Optional[float] = None
        progress: Optional[float] = None
        graduated = False
        if reserves is not None:
            mon_reserve, token_reserve = reserves
            mon_raised = mon_reserve
            progress = max(0.0, min(100.0, mon_reserve / GRADUATION_MON * 100.0))
            # Curve empty of tokens -> sold out / migrated to DEX.
            graduated = token_reserve <= 0 and mon_reserve <= 0

        return CurveInfo(
            token_address=token_address,
            is_incubating=not graduated,
            progress_pct=progress,
            mon_raised=mon_raised,
            graduated=graduated,
            curve_address=curve_address,
        )
    except Exception as exc:  # noqa: BLE001 - never raise
        logger.warning("get_curve_info(%s) failed: %s", token_address, exc)
        return _unknown(str(token_address))
