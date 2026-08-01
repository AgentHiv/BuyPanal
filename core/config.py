"""Environment configuration for the Monad buy bot (SPEC 5.2)."""

from __future__ import annotations

import os
from dataclasses import dataclass

try:  # python-dotenv is optional: never fail if it is missing
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - depends on environment
    def load_dotenv(*_args, **_kwargs):  # type: ignore
        return False


DEFAULT_BUY_URL_TEMPLATE = "https://pancakeswap.finance/swap?outputCurrency={token}"


@dataclass
class Config:
    TELEGRAM_TOKEN: str
    MONAD_RPC_URL: str = "https://rpc.monad.xyz"
    MONAD_CHAIN_ID: int = 143
    DB_PATH: str = "data/bot.db"
    POLL_INTERVAL: float = 4.0          # seconds
    BLOCKS_PER_POLL: int = 50
    MON_USD_PRICE: float = 0.0          # 0 = unknown/None in alerts
    EXPLORER_URL: str = "https://monadvision.com"
    BUY_URL_TEMPLATE: str = DEFAULT_BUY_URL_TEMPLATE  # e.g. "https://nad.fun/token/{token}"
    NAD_FUN_LENS: str = ""              # optional lens/factory address, "" if unused
    SCANNER_ENABLED: bool = True        # SPEC-v2: new-token scanner on/off
    PAIR_FACTORIES: str = ""            # SPEC-v2: comma-separated UniV2 factory addresses, "" = auto


def _get_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _get_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def load_config() -> Config:
    """Build a Config from .env / environment variables.

    Every field except TELEGRAM_TOKEN has a sane default so the bot can
    start with a .env containing only TELEGRAM_TOKEN.
    """
    load_dotenv()

    return Config(
        TELEGRAM_TOKEN=os.environ.get("TELEGRAM_TOKEN", ""),
        MONAD_RPC_URL=os.environ.get("MONAD_RPC_URL", "https://rpc.monad.xyz"),
        MONAD_CHAIN_ID=_get_int("MONAD_CHAIN_ID", 143),
        DB_PATH=os.environ.get("DB_PATH", "data/bot.db"),
        POLL_INTERVAL=_get_float("POLL_INTERVAL", 4.0),
        BLOCKS_PER_POLL=_get_int("BLOCKS_PER_POLL", 50),
        MON_USD_PRICE=_get_float("MON_USD_PRICE", 0.0),
        EXPLORER_URL=os.environ.get("EXPLORER_URL", "https://monadvision.com"),
        BUY_URL_TEMPLATE=os.environ.get("BUY_URL_TEMPLATE", DEFAULT_BUY_URL_TEMPLATE),
        NAD_FUN_LENS=os.environ.get("NAD_FUN_LENS", ""),
        SCANNER_ENABLED=_get_bool("SCANNER_ENABLED", True),
        PAIR_FACTORIES=os.environ.get("PAIR_FACTORIES", ""),
    )
