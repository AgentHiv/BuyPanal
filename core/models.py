"""Shared dataclasses (contracts) for the Monad buy bot.

These definitions follow SPEC.md section 5.1 exactly. Do not change
field names, types or defaults — other modules depend on them.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TokenInfo:
    address: str            # checksummed
    name: str
    symbol: str
    decimals: int
    total_supply: float     # human units
    kind: str = "unknown"   # "dex" | "curve" | "unknown"


@dataclass
class BuyEvent:
    token_address: str
    token_symbol: str
    token_name: str
    buyer: str
    amount_token: float        # human units received
    amount_mon: float          # MON spent (0.0 if unknown)
    amount_usd: Optional[float]  # None if no USD feed
    price_mon: float           # unit price in MON (0.0 if unknown)
    tx_hash: str               # 0x...
    pair_address: str          # pair/pool/curve the tokens came from
    kind: str                  # "dex" | "curve"
    block_number: int
    timestamp: int             # unix seconds


@dataclass
class GroupSettings:
    chat_id: int
    language: str = "en"        # en | es | zh
    buy_emoji: str = "🟢"
    whale_emoji: str = "🐋"
    min_buy_mon: float = 1.0    # min MON to alert
    whale_mon: float = 100.0    # whale threshold
    emoji_step_mon: float = 10.0  # 1 emoji repeated per this many MON


@dataclass
class CurveInfo:
    token_address: str
    is_incubating: bool         # still on bonding curve
    progress_pct: Optional[float]   # 0-100 toward graduation, None if unknown
    mon_raised: Optional[float]
    graduated: bool
    curve_address: Optional[str] = None
