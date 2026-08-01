"""Quote-token registry and quote-leg -> MON conversion.

A "quote" is the non-token leg of a DEX pair (WMON, USDC, USDT0, WETH...).
The registry maps lowercase token address -> QuoteToken and is extensible
via ``Config.QUOTE_TOKENS`` (CSV of ``SYMBOL:0xaddress`` entries).

Conversion rules:
- ``native`` (WMON): raw amount / 10**decimals is already MON.
- ``stable`` (USDC/USDT0): raw amount / 10**decimals is USD; MON is derived
  via ``Config.MON_USD_PRICE`` when > 0 (otherwise amount_mon = 0.0 but the
  USD value is preserved).
- ``unpriced`` (WETH, custom entries without a USD/stable hint): no reliable
  conversion — callers fall back to the legacy heuristic.

This module never raises.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from chain.abis import USDC_ADDRESS, USDT_ADDRESS, WETH_ADDRESS, WMON_ADDRESS

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QuoteToken:
    symbol: str
    decimals: int
    kind: str  # "native" | "stable" | "unpriced"


# Built-in registry (Monad mainnet, verified on-chain — see chain/abis.py).
_DEFAULT_QUOTES: dict[str, QuoteToken] = {
    WMON_ADDRESS.lower(): QuoteToken("WMON", 18, "native"),
    USDC_ADDRESS.lower(): QuoteToken("USDC", 6, "stable"),
    USDT_ADDRESS.lower(): QuoteToken("USDT0", 6, "stable"),
    WETH_ADDRESS.lower(): QuoteToken("WETH", 18, "unpriced"),
}


def _classify_custom(symbol: str) -> tuple[int, str]:
    """Guess (decimals, kind) for a QUOTE_TOKENS entry from its symbol."""
    sym = symbol.upper()
    if "MON" in sym:
        return 18, "native"
    if "USD" in sym:
        return 6, "stable"
    return 18, "unpriced"


def _config_quote_tokens() -> str:
    try:
        from core.config import load_config

        return str(load_config().QUOTE_TOKENS or "")
    except Exception:  # noqa: BLE001
        return ""


def get_quote_tokens() -> dict[str, QuoteToken]:
    """Built-in quotes plus ``Config.QUOTE_TOKENS`` extras. Never raises."""
    quotes = dict(_DEFAULT_QUOTES)
    raw = _config_quote_tokens()
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        symbol, _, address = entry.partition(":")
        symbol = symbol.strip()
        address = address.strip().lower()
        if not symbol or not address.startswith("0x") or len(address) != 42:
            logger.warning("ignoring malformed QUOTE_TOKENS entry: %r", entry)
            continue
        try:
            decimals, kind = _classify_custom(symbol)
            quotes[address] = QuoteToken(symbol, decimals, kind)
        except Exception:  # noqa: BLE001
            logger.warning("ignoring QUOTE_TOKENS entry: %r", entry)
    return quotes


def quote_leg_to_mon(
    quote: QuoteToken, raw_amount: int, mon_usd_price: float
) -> tuple[float, Optional[float]]:
    """Convert a raw quote-leg amount to (amount_mon, amount_usd).

    ``amount_usd`` is None unless the quote is a stablecoin. For unpriced
    quotes returns (0.0, None) so callers fall back to the heuristic.
    Never raises.
    """
    try:
        raw_amount = int(raw_amount)
        if raw_amount <= 0:
            return 0.0, None
        if quote.kind == "native":
            return raw_amount / (10 ** quote.decimals), None
        if quote.kind == "stable":
            usd = raw_amount / (10 ** quote.decimals)
            mon = usd / mon_usd_price if mon_usd_price > 0 else 0.0
            return mon, usd
        return 0.0, None  # unpriced quote (e.g. WETH): no conversion
    except Exception:  # noqa: BLE001
        return 0.0, None
