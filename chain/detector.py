"""Classify an ERC20 Transfer of a tracked token into a BuyEvent.

A BUY is: tokens moved FROM a contract (UniV2 pair / UniV3 pool / bonding
curve) TO a buyer, and the same transaction contains a matching
UniV2 Swap / UniV3 Swap / curve Buy event.

Sells, mints and plain wallet transfers return None. This module never
raises on unexpected on-chain data — it returns None instead.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from core.models import BuyEvent, SellEvent, TokenInfo

from chain.abis import (
    CURVE_BUY_TOPIC,
    CURVE_SELL_TOPIC,
    UNIV2_PAIR_ABI,
    UNIV2_SWAP_TOPIC,
    UNIV3_SWAP_TOPIC,
    ZERO_ADDRESS,
)
from chain.quotes import QuoteToken, get_quote_tokens, quote_leg_to_mon

logger = logging.getLogger(__name__)

_UINT256_BYTES = 32


# ---------------------------------------------------------------------------
# Low-level decoding helpers (tolerant of dicts, AttributeDicts, HexBytes)
# ---------------------------------------------------------------------------


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Read a key from a dict-like or attribute-like object."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _hex(value: Any) -> str:
    """Normalize bytes/HexBytes/str to a lowercase 0x-prefixed hex string."""
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray)):
        return "0x" + bytes(value).hex()
    text = str(value)
    if not text.startswith("0x"):
        text = "0x" + text
    return text.lower()


def _topic_address(topic: Any) -> str:
    """Extract an address from a 32-byte indexed topic."""
    raw = _hex(topic)
    if len(raw) < 42:
        return raw
    return "0x" + raw[-40:]


def _data_words(data: Any) -> list[bytes]:
    """Split log data into 32-byte words."""
    if data is None:
        return []
    if isinstance(data, (bytes, bytearray)):
        raw = bytes(data)
    else:
        raw = bytes.fromhex(str(data).removeprefix("0x"))
    return [raw[i : i + _UINT256_BYTES] for i in range(0, len(raw), _UINT256_BYTES) if len(raw[i : i + _UINT256_BYTES]) == _UINT256_BYTES]


def _word_uint(word: bytes) -> int:
    return int.from_bytes(word, "big")


def _word_int(word: bytes) -> int:
    """Decode a 32-byte word as a signed int256."""
    value = _word_uint(word)
    if value >= 1 << 255:
        value -= 1 << 256
    return value


def _mon_usd_price() -> float:
    """Configured MON/USD price; 0.0 when unset or unreadable."""
    try:
        from core.config import load_config

        return float(load_config().MON_USD_PRICE or 0.0)
    except Exception:  # noqa: BLE001
        return 0.0


def _mon_usd(amount_mon: float) -> Optional[float]:
    """USD value of a MON amount; None when no USD feed is configured."""
    mon_usd_price = _mon_usd_price()
    if mon_usd_price <= 0 or amount_mon <= 0:
        return None
    return amount_mon * mon_usd_price


# ---------------------------------------------------------------------------
# UniV2 pair quote-leg resolution (token0/token1, cached per pair address)
# ---------------------------------------------------------------------------

# pair_address.lower() -> (token0.lower(), token1.lower()) or None when the
# pair could not be queried. TTL: infinite (pair tokens are immutable).
_PAIR_TOKENS_CACHE: dict[str, Optional[tuple[str, str]]] = {}


async def _pair_tokens(w3: Any, pair_address: str) -> Optional[tuple[str, str]]:
    """(token0, token1) of a UniV2 pair, lowercase; None on any failure."""
    key = str(pair_address or "").lower()
    if not key:
        return None
    if key in _PAIR_TOKENS_CACHE:
        return _PAIR_TOKENS_CACHE[key]
    tokens: Optional[tuple[str, str]] = None
    try:
        from web3 import Web3

        contract = w3.eth.contract(
            address=Web3.to_checksum_address(key), abi=UNIV2_PAIR_ABI
        )
        token0 = await contract.functions.token0().call()
        token1 = await contract.functions.token1().call()
        if token0 and token1:
            tokens = (str(token0).lower(), str(token1).lower())
    except Exception as exc:  # noqa: BLE001 - fall back to heuristic
        logger.debug("token0/token1 lookup failed for pair %s: %s", key, exc)
    _PAIR_TOKENS_CACHE[key] = tokens
    return tokens


async def _quote_leg(
    w3: Any, pair_address: str, token_address: str
) -> Optional[tuple[int, QuoteToken]]:
    """Index (0 or 1) of the known-quote leg of a UniV2 pair, plus the quote.

    Returns None when the pair tokens cannot be read or neither leg is a
    known quote token — callers then fall back to the legacy heuristic.
    """
    tokens = await _pair_tokens(w3, pair_address)
    if tokens is None:
        return None
    quotes = get_quote_tokens()
    token_lc = (token_address or "").lower()
    # Prefer the leg that is NOT the tracked token (a tracked token may
    # itself be a quote token, e.g. USDC in a USDC/WMON pair).
    order = (0, 1)
    if token_lc and tokens[0] == token_lc:
        order = (1, 0)
    for idx in order:
        quote = quotes.get(tokens[idx])
        if quote is not None:
            return idx, quote
    return None


# ---------------------------------------------------------------------------
# Swap-event classification
# ---------------------------------------------------------------------------


async def _classify_swap_log(w3: Any, log: Any, token_address: str) -> Optional[dict]:
    """If log is a relevant swap/curve event, return decoded info, else None.

    Returned dict: {"kind": "dex"|"curve", "emitter": str, "mon_in": float,
    "usd_in": Optional[float]} where ``usd_in`` is set when the quote leg is
    a known stablecoin (USD value known even without a MON/USD feed).
    """
    topics = _get(log, "topics") or []
    if not topics:
        return None
    topic0 = _hex(topics[0])
    emitter = str(_get(log, "address", "") or "").lower()
    words = _data_words(_get(log, "data", b""))

    if topic0 == UNIV2_SWAP_TOPIC and len(words) >= 4:
        amount0_in = _word_uint(words[0])
        amount1_in = _word_uint(words[1])
        # Precise path: read the pair's token0/token1 to find the quote leg.
        leg = await _quote_leg(w3, emitter, token_address)
        if leg is not None:
            idx, quote = leg
            raw_quote = amount0_in if idx == 0 else amount1_in
            mon_in, usd_in = quote_leg_to_mon(quote, raw_quote, _mon_usd_price())
            if mon_in > 0.0 or usd_in is not None:
                return {"kind": "dex", "emitter": emitter, "mon_in": mon_in, "usd_in": usd_in}
            # Unpriced quote (e.g. WETH): fall through to the heuristic.
        # Heuristic: the larger input side is the MON leg of the swap
        # (WMON has 18 decimals).
        mon_in = max(amount0_in, amount1_in) / 1e18
        return {"kind": "dex", "emitter": emitter, "mon_in": mon_in, "usd_in": None}

    if topic0 == UNIV3_SWAP_TOPIC and len(words) >= 2:
        amount0 = _word_int(words[0])
        amount1 = _word_int(words[1])
        # Positive amount = tokens entering the pool = MON spent on a buy.
        mon_in = max(amount0, amount1, 0) / 1e18
        return {"kind": "dex", "emitter": emitter, "mon_in": mon_in, "usd_in": None}

    if topic0 == CURVE_BUY_TOPIC and len(words) >= 2:
        # The token is an indexed arg; its position varies by deployment:
        # real nad.fun curves index (token, to) — verified on-chain — while
        # other deployments index (sender, token). Accept either position.
        if token_address and len(topics) >= 3:
            indexed = {_topic_address(t) for t in topics[1:3]}
            if token_address.lower() not in indexed:
                return None
        amount_in = _word_uint(words[0])  # MON/WMON spent
        return {"kind": "curve", "emitter": emitter, "mon_in": amount_in / 1e18, "usd_in": None}

    return None


async def _classify_sell_swap_log(w3: Any, log: Any, token_address: str) -> Optional[dict]:
    """Sell-side mirror of ``_classify_swap_log``.

    Returned dict: {"kind": "dex"|"curve", "emitter": str, "mon_out": float,
    "usd_out": Optional[float]} where ``mon_out`` is the quote leg LEAVING
    the pair/pool/curve (what the seller received) and ``usd_out`` is set
    when the quote leg is a known stablecoin.
    """
    topics = _get(log, "topics") or []
    if not topics:
        return None
    topic0 = _hex(topics[0])
    emitter = str(_get(log, "address", "") or "").lower()
    words = _data_words(_get(log, "data", b""))

    if topic0 == UNIV2_SWAP_TOPIC and len(words) >= 4:
        amount0_out = _word_uint(words[2])
        amount1_out = _word_uint(words[3])
        # Precise path: read the pair's token0/token1 to find the quote leg.
        leg = await _quote_leg(w3, emitter, token_address)
        if leg is not None:
            idx, quote = leg
            raw_quote = amount0_out if idx == 0 else amount1_out
            mon_out, usd_out = quote_leg_to_mon(quote, raw_quote, _mon_usd_price())
            if mon_out > 0.0 or usd_out is not None:
                return {"kind": "dex", "emitter": emitter, "mon_out": mon_out, "usd_out": usd_out}
            # Unpriced quote (e.g. WETH): fall through to the heuristic.
        # Heuristic mirrors the buy side: the larger output leg is the
        # MON leg.
        mon_out = max(amount0_out, amount1_out) / 1e18
        return {"kind": "dex", "emitter": emitter, "mon_out": mon_out, "usd_out": None}

    if topic0 == UNIV3_SWAP_TOPIC and len(words) >= 2:
        amount0 = _word_int(words[0])
        amount1 = _word_int(words[1])
        # Negative amount = tokens leaving the pool = MON received on a sell.
        mon_out = abs(min(amount0, amount1, 0)) / 1e18
        return {"kind": "dex", "emitter": emitter, "mon_out": mon_out, "usd_out": None}

    if topic0 == CURVE_SELL_TOPIC and len(words) >= 2:
        # Same indexed-token tolerance as the buy side (see above).
        if token_address and len(topics) >= 3:
            indexed = {_topic_address(t) for t in topics[1:3]}
            if token_address.lower() not in indexed:
                return None
        amount_out = _word_uint(words[1])  # MON/WMON received
        return {"kind": "curve", "emitter": emitter, "mon_out": amount_out / 1e18, "usd_out": None}

    return None


async def _tx_value_mon(w3: Any, tx_hash: Any) -> float:
    """Native MON value sent with the transaction (0.0 on any failure)."""
    try:
        tx = await w3.eth.get_transaction(tx_hash)
        value = _get(tx, "value", 0) or 0
        return int(value) / 1e18
    except Exception:  # noqa: BLE001
        return 0.0


async def _block_timestamp(w3: Any, block_number: Any) -> int:
    try:
        block = await w3.eth.get_block(block_number)
        ts = _get(block, "timestamp")
        if ts is not None:
            return int(ts)
    except Exception:  # noqa: BLE001
        pass
    return int(time.time())


# ---------------------------------------------------------------------------
# Public interface (SPEC 5.8)
# ---------------------------------------------------------------------------


async def build_buy_event(w3, transfer_log, token_info: TokenInfo) -> BuyEvent | None:
    """Given an ERC20 Transfer log of the tracked token, decide if it is a BUY.

    Buy = tokens moved FROM a contract (pair/pool/curve) TO an EOA/contract
    buyer, AND the same tx contains a UniV2 Swap / UniV3 Swap / curve Buy
    event. Returns None for sells, plain wallet transfers, mints to pair,
    etc. Never raises.
    """
    try:
        topics = _get(transfer_log, "topics") or []
        if len(topics) < 3:
            return None
        from_addr = _topic_address(topics[1])
        to_addr = _topic_address(topics[2])
        if from_addr in ("", ZERO_ADDRESS.lower()) or to_addr in ("", ZERO_ADDRESS.lower()):
            return None  # mint / burn, not a buy

        words = _data_words(_get(transfer_log, "data", b""))
        if not words:
            return None
        raw_amount = _word_uint(words[0])
        decimals = int(getattr(token_info, "decimals", 18) or 18)
        amount_token = raw_amount / (10**decimals)
        if amount_token <= 0:
            return None

        tx_hash = _get(transfer_log, "transactionHash") or _get(transfer_log, "transaction_hash")
        if tx_hash is None:
            return None
        block_number = int(_get(transfer_log, "blockNumber", _get(transfer_log, "block_number", 0)) or 0)

        try:
            receipt = await w3.eth.get_transaction_receipt(tx_hash)
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_transaction_receipt failed for %s: %s", _hex(tx_hash), exc)
            return None
        if receipt is None:
            return None

        logs = _get(receipt, "logs", []) or []
        token_address = str(getattr(token_info, "address", "") or "").lower()

        buy_candidate: Optional[dict] = None
        saw_sell = False
        for log in logs:
            info = await _classify_swap_log(w3, log, token_address)
            if info is None:
                continue
            if info["emitter"] == from_addr:
                buy_candidate = info  # tokens left the pair/pool/curve -> buy
                break
            if info["emitter"] == to_addr:
                saw_sell = True  # tokens entered the pair/pool/curve -> sell

        if buy_candidate is None:
            # Either a sell, or a transfer unrelated to any swap in this tx.
            if saw_sell:
                logger.debug("sell detected in tx %s", _hex(tx_hash))
            return None

        amount_mon = float(buy_candidate["mon_in"])
        usd_in = buy_candidate.get("usd_in")
        if amount_mon <= 0.0 and usd_in is None:
            # Fallback: native MON value sent with the tx. Skipped when the
            # quote leg is a stablecoin (tx.value would be misleading there).
            amount_mon = await _tx_value_mon(w3, tx_hash)

        price_mon = amount_mon / amount_token if amount_token > 0 and amount_mon > 0 else 0.0
        timestamp = await _block_timestamp(w3, block_number)

        return BuyEvent(
            token_address=getattr(token_info, "address", ""),
            token_symbol=getattr(token_info, "symbol", ""),
            token_name=getattr(token_info, "name", ""),
            buyer=to_addr,
            amount_token=amount_token,
            amount_mon=amount_mon,
            amount_usd=usd_in if usd_in is not None else _mon_usd(amount_mon),
            price_mon=price_mon,
            tx_hash=_hex(tx_hash),
            pair_address=buy_candidate["emitter"],
            kind=buy_candidate["kind"],
            block_number=block_number,
            timestamp=timestamp,
        )
    except Exception as exc:  # noqa: BLE001 - never raise on unexpected data
        logger.warning("build_buy_event failed: %s", exc)
        return None


async def build_sell_event(w3, transfer_log, token_info: TokenInfo) -> SellEvent | None:
    """Given an ERC20 Transfer log of the tracked token, decide if it is a SELL.

    Sell = tokens moved FROM an EOA/contract TOWARDS the pair/pool/curve,
    AND the same transaction contains a matching UniV2 Swap / UniV3 Swap /
    curve Sell event. ``amount_mon`` is the MON leg received by the seller.
    Returns None for buys, plain wallet transfers, burns, etc. Never raises.
    """
    try:
        topics = _get(transfer_log, "topics") or []
        if len(topics) < 3:
            return None
        from_addr = _topic_address(topics[1])
        to_addr = _topic_address(topics[2])
        if from_addr in ("", ZERO_ADDRESS.lower()) or to_addr in ("", ZERO_ADDRESS.lower()):
            return None  # mint / burn, not a sell

        words = _data_words(_get(transfer_log, "data", b""))
        if not words:
            return None
        raw_amount = _word_uint(words[0])
        decimals = int(getattr(token_info, "decimals", 18) or 18)
        amount_token = raw_amount / (10**decimals)
        if amount_token <= 0:
            return None

        tx_hash = _get(transfer_log, "transactionHash") or _get(transfer_log, "transaction_hash")
        if tx_hash is None:
            return None
        block_number = int(_get(transfer_log, "blockNumber", _get(transfer_log, "block_number", 0)) or 0)

        try:
            receipt = await w3.eth.get_transaction_receipt(tx_hash)
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_transaction_receipt failed for %s: %s", _hex(tx_hash), exc)
            return None
        if receipt is None:
            return None

        logs = _get(receipt, "logs", []) or []
        token_address = str(getattr(token_info, "address", "") or "").lower()

        sell_candidate: Optional[dict] = None
        for log in logs:
            info = await _classify_sell_swap_log(w3, log, token_address)
            if info is None:
                continue
            if info["emitter"] == to_addr:
                sell_candidate = info  # tokens entered the pair/pool/curve -> sell
                break

        if sell_candidate is None:
            return None

        amount_mon = float(sell_candidate["mon_out"])  # 0.0 = unknown
        usd_out = sell_candidate.get("usd_out")
        price_mon = amount_mon / amount_token if amount_token > 0 and amount_mon > 0 else 0.0
        timestamp = await _block_timestamp(w3, block_number)

        return SellEvent(
            token_address=getattr(token_info, "address", ""),
            token_symbol=getattr(token_info, "symbol", ""),
            token_name=getattr(token_info, "name", ""),
            buyer=from_addr,  # the seller
            amount_token=amount_token,
            amount_mon=amount_mon,
            amount_usd=usd_out if usd_out is not None else _mon_usd(amount_mon),
            price_mon=price_mon,
            tx_hash=_hex(tx_hash),
            pair_address=sell_candidate["emitter"],
            kind=sell_candidate["kind"],
            block_number=block_number,
            timestamp=timestamp,
        )
    except Exception as exc:  # noqa: BLE001 - never raise on unexpected data
        logger.warning("build_sell_event failed: %s", exc)
        return None
