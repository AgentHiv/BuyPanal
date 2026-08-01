"""Token metadata, price (in MON) and market cap.

Price strategy: find the best WMON liquidity — try the bonding curve
(Sync/reserves) first, then UniV2-style pairs discovered from recent
Transfer senders (contracts holding the token). If no liquidity is found,
return 0.0. This module never raises on missing liquidity or bad metadata.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from web3 import Web3

from core.models import TokenInfo

from chain.abis import (
    ERC20_ABI,
    PAIR_OR_CURVE_ABI,
    TRANSFER_TOPIC,
    UNIV2_PAIR_ABI,
)
from chain.client import get_w3
from chain.detector import _get, _mon_usd_price, _topic_address
from chain.quotes import get_quote_tokens

logger = logging.getLogger(__name__)

# How far back to scan for candidate pairs/curves from Transfer senders.
_PAIR_SCAN_BLOCKS = 20_000
_MAX_CANDIDATES = 10


async def _safe_call(contract: Any, fn: str, *args: Any) -> Any:
    try:
        return await getattr(contract.functions, fn)(*args).call()
    except Exception:  # noqa: BLE001
        return None


async def get_token_info(address: str) -> TokenInfo:
    """ERC20 metadata via RPC. Falls back to safe defaults; never raises."""
    name = ""
    symbol = ""
    decimals = 18
    total_supply = 0.0
    try:
        checksummed = Web3.to_checksum_address(address)
    except Exception:  # noqa: BLE001
        checksummed = str(address)

    try:
        w3 = get_w3()
        token = w3.eth.contract(address=checksummed, abi=ERC20_ABI)
        raw_name = await _safe_call(token, "name")
        raw_symbol = await _safe_call(token, "symbol")
        raw_decimals = await _safe_call(token, "decimals")
        raw_supply = await _safe_call(token, "totalSupply")

        name = str(raw_name) if raw_name else ""
        symbol = str(raw_symbol) if raw_symbol else ""
        if raw_decimals is not None:
            try:
                decimals = int(raw_decimals)
            except (TypeError, ValueError):
                decimals = 18
        if raw_supply is not None:
            total_supply = float(raw_supply) / (10**decimals)
    except Exception as exc:  # noqa: BLE001 - never raise on bad metadata
        logger.warning("get_token_info(%s) metadata failed: %s", address, exc)

    kind = "unknown"
    try:
        from chain.incubation import get_curve_info

        curve = await get_curve_info(checksummed)
        if curve.is_incubating:
            kind = "curve"
        elif curve.graduated:
            kind = "dex"
    except Exception:  # noqa: BLE001
        pass

    if kind == "unknown":
        try:
            w3 = get_w3()
            if await _has_known_pair(w3, checksummed):
                kind = "dex"
        except Exception:  # noqa: BLE001
            pass

    return TokenInfo(
        address=checksummed,
        name=name,
        symbol=symbol,
        decimals=decimals,
        total_supply=total_supply,
        kind=kind,
    )


async def _recent_transfer_senders(w3: Any, token_address: str) -> list[str]:
    """Unique contracts that recently sent the token (pair/pool/curve).

    Scans newest-first in block windows (public RPCs reject large ranges)
    and stops as soon as enough candidates are found.
    """
    try:
        from chain.client import iter_logs_windowed

        latest = int(await w3.eth.get_block_number())
        from_block = max(0, latest - _PAIR_SCAN_BLOCKS)
        senders: list[str] = []
        async for logs in iter_logs_windowed(
            w3,
            {"address": token_address, "topics": [TRANSFER_TOPIC]},
            from_block,
            latest,
            max_windows=60,
        ):
            for log in reversed(logs):
                topics = _get(log, "topics") or []
                if len(topics) >= 2:
                    sender = _topic_address(topics[1])
                    if sender and sender not in senders:
                        senders.append(sender)
            if len(senders) >= _MAX_CANDIDATES:
                break
        return senders[:_MAX_CANDIDATES]
    except Exception as exc:  # noqa: BLE001
        logger.debug("transfer scan failed for %s: %s", token_address, exc)
        return []


async def _has_known_pair(w3: Any, token_address: str) -> bool:
    """True when a recent Transfer sender is a UniV2 pair of (token, quote)."""
    quotes = get_quote_tokens()
    token_lc = token_address.lower()
    for candidate in await _recent_transfer_senders(w3, token_address):
        try:
            contract = w3.eth.contract(
                address=Web3.to_checksum_address(candidate), abi=UNIV2_PAIR_ABI
            )
            token0 = await _safe_call(contract, "token0")
            token1 = await _safe_call(contract, "token1")
            if not token0 or not token1:
                continue
            legs = {str(token0).lower(), str(token1).lower()}
            if token_lc in legs and (legs - {token_lc}) & set(quotes):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


async def _price_from_candidate(w3: Any, candidate: str, token_address: str) -> float:
    """Spot price in MON from a UniV2-pair/curve-like contract, else 0.0."""
    try:
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(candidate), abi=PAIR_OR_CURVE_ABI
        )
        reserves = await _safe_call(contract, "getReserves")
        reserve0 = reserve1 = None
        if reserves is not None:
            reserve0, reserve1 = float(reserves[0]), float(reserves[1])
        else:
            reserves = await _safe_call(contract, "reserves")
            if reserves is not None:
                # curve reserves() -> (reserveMon, reserveToken)
                reserve_mon, reserve_token = float(reserves[0]), float(reserves[1])
                if reserve_token > 0:
                    return reserve_mon / reserve_token
                return 0.0
        if reserve0 is None or reserve1 is None or reserve0 <= 0 or reserve1 <= 0:
            return 0.0

        token0 = await _safe_call(contract, "token0")
        token1 = await _safe_call(contract, "token1")
        token_lc = token_address.lower()
        quotes = get_quote_tokens()
        token0_lc = str(token0).lower() if token0 else ""
        token1_lc = str(token1).lower() if token1 else ""
        if token0_lc == token_lc and token1_lc in quotes:
            token_reserve, quote_reserve = reserve0, reserve1
            quote = quotes[token1_lc]
        elif token1_lc == token_lc and token0_lc in quotes:
            token_reserve, quote_reserve = reserve1, reserve0
            quote = quotes[token0_lc]
        else:
            return 0.0  # not a known-quote pair for this token

        try:
            token_contract = w3.eth.contract(
                address=Web3.to_checksum_address(token_address), abi=ERC20_ABI
            )
            raw_decimals = await _safe_call(token_contract, "decimals")
            token_decimals = int(raw_decimals) if raw_decimals is not None else 18
        except Exception:  # noqa: BLE001 - default to 18 decimals
            token_decimals = 18
        price_in_quote = (quote_reserve / (10 ** quote.decimals)) / (
            token_reserve / (10 ** token_decimals)
        )

        if quote.kind == "native":
            # WMON leg: the quote unit IS MON (identical to the raw reserve
            # ratio when the token also has 18 decimals — legacy behavior).
            return price_in_quote
        if quote.kind == "stable":
            # USDC/USDT0 leg: USD price -> MON via the configured feed.
            mon_usd_price = _mon_usd_price()
            if mon_usd_price <= 0:
                return 0.0
            return price_in_quote / mon_usd_price
        return 0.0  # unpriced quote (e.g. WETH): no MON conversion
    except Exception:  # noqa: BLE001
        return 0.0


async def get_price_mon(address: str) -> float:
    """Token price in MON. 0.0 if no liquidity found. Never raises."""
    try:
        token_address = Web3.to_checksum_address(address)
    except Exception:  # noqa: BLE001
        return 0.0

    try:
        w3 = get_w3()
        # 1) Bonding curve reserves (Sync-style), if the token is incubating.
        try:
            from chain.incubation import _curve_reserves, _find_curve_address

            curve_address = await _find_curve_address(w3, token_address)
            if curve_address is not None:
                reserves = await _curve_reserves(w3, curve_address)
                if reserves is not None:
                    mon_reserve, token_reserve = reserves
                    if token_reserve > 0 and mon_reserve > 0:
                        return mon_reserve / token_reserve
        except Exception:  # noqa: BLE001
            pass

        # 2) UniV2-style pairs discovered from recent Transfer senders.
        best = 0.0
        for candidate in await _recent_transfer_senders(w3, token_address):
            price = await _price_from_candidate(w3, candidate, token_address)
            if price > best:
                best = price
        return best
    except Exception as exc:  # noqa: BLE001 - never raise
        logger.warning("get_price_mon(%s) failed: %s", address, exc)
        return 0.0


async def get_mcap_mon(address: str) -> float:
    """Market cap in MON (price * total_supply). 0.0 if unknown."""
    try:
        price = await get_price_mon(address)
        if price <= 0:
            return 0.0
        info = await get_token_info(address)
        if info.total_supply <= 0:
            return 0.0
        return price * info.total_supply
    except Exception as exc:  # noqa: BLE001 - never raise
        logger.warning("get_mcap_mon(%s) failed: %s", address, exc)
        return 0.0
