# SPEC.md — Monad Buy Bot (Telegram)

**Single source of truth.** Every module implements EXACTLY the interfaces defined here.

## 1. Product Summary

A Telegram buy-alert bot (BuyBotTech-style) for the **Monad** blockchain (EVM-compatible).
- Tracks **any ERC-20 token** on Monad: DEX-listed tokens AND **incubation tokens** (bonding-curve / launchpad tokens, nad.fun-style).
- Posts buy alerts to Telegram groups with **customizable emojis**, whale alerts, and per-group settings.
- **3 languages**: English (`en`), Español (`es`), 中文 (`zh`). Commands and their descriptions are registered in **English**; bot messages follow the group's configured language.

## 2. Chain Facts (verified)

- Monad mainnet: **chain ID 143**, RPC `https://rpc.monad.xyz`, explorer `https://monadvision.com`, native token **MON** (18 decimals).
- Monad testnet: chain ID 10143, RPC `https://testnet-rpc.monad.xyz`.
- Monad is fully EVM-compatible → use standard `web3.py` tooling.
- DEXs on Monad: PancakeSwap v2/v3, Uniswap v3, Kuru (all standard EVM event signatures).
- nad.fun-style launchpads: token trades on a **bonding curve** ("incubation") until graduation (~225,000 MON raised, ~80% supply sold), then liquidity moves to a DEX.
- Curve `Buy` event signature: `Buy(address indexed sender, address indexed token, uint256 amountIn, uint256 amountOut)` where `amountIn` is WMON/MON spent.
- WMON (wrapped MON) is the quote currency on DEX pairs.

## 3. Tech Stack

- Python 3.11+
- `python-telegram-bot` v21+ (async)
- `web3.py` v7+ (async HTTP provider)
- SQLite via `sqlite3` (synchronous is fine; wrap in helpers)
- Env config via `python-dotenv`
- Tests: `pytest` + `pytest-asyncio` (chain mocked, no live RPC in tests)

## 4. Repository Layout

```
monad-buy-bot/
├── bot/
│   ├── __init__.py
│   ├── main.py            # builds Application, registers handlers, starts listener
│   ├── handlers.py        # all command handlers
│   ├── notifier.py        # buy alert message formatting + sending
│   └── keyboards.py       # inline buttons (tx / chart / buy links)
├── chain/
│   ├── __init__.py
│   ├── abis.py            # minimal ABIs (ERC20, UniV2 pair, UniV3 pool, curve)
│   ├── client.py          # async web3 client + RPC failover
│   ├── listener.py        # block polling + eth_getLogs, emits BuyEvent
│   ├── detector.py        # classifies a Transfer into a BuyEvent (dex/curve)
│   ├── incubation.py      # bonding-curve progress / graduation status
│   └── price.py           # token metadata, price, market cap
├── core/
│   ├── __init__.py
│   ├── config.py          # env configuration
│   ├── db.py              # SQLite persistence
│   ├── i18n.py            # translation loader
│   └── models.py          # dataclasses (shared contracts)
├── locales/
│   ├── en.json
│   ├── es.json
│   └── zh.json
├── tests/
│   ├── test_i18n.py
│   ├── test_db.py
│   ├── test_detector.py
│   └── test_notifier.py
├── main.py                # thin entrypoint: from bot.main import run; run()
├── requirements.txt
├── .env.example
├── .gitignore
├── Dockerfile
├── docker-compose.yml
├── README.md              # English
└── README.es.md           # Español
```

## 5. Interface Contracts (SACRED — do not change)

### 5.1 `core/models.py`

```python
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
```

### 5.2 `core/config.py`

```python
class Config:
    TELEGRAM_TOKEN: str
    MONAD_RPC_URL: str            # default https://rpc.monad.xyz
    MONAD_CHAIN_ID: int           # default 143
    DB_PATH: str                  # default "data/bot.db"
    POLL_INTERVAL: float          # seconds, default 4.0
    BLOCKS_PER_POLL: int          # default 50
    MON_USD_PRICE: float          # default 0.0 (0 = unknown/None in alerts)
    EXPLORER_URL: str             # default https://monadvision.com
    BUY_URL_TEMPLATE: str         # e.g. "https://nad.fun/token/{token}" ; fallback pancakeswap
    NAD_FUN_LENS: str             # optional lens/factory address, "" if unused

def load_config() -> Config: ...  # reads .env / environment
```

`.env.example` must document every field above.

### 5.3 `core/db.py`

```python
class Database:
    def __init__(self, path: str) -> None: ...
    # groups
    def get_settings(self, chat_id: int) -> GroupSettings: ...          # defaults if new
    def save_settings(self, settings: GroupSettings) -> None: ...
    # tracked tokens
    def add_token(self, chat_id: int, address: str, kind: str = "unknown") -> bool: ...  # False if exists
    def remove_token(self, chat_id: int, address: str) -> bool: ...     # False if missing
    def list_tokens(self, chat_id: int) -> list[str]: ...               # addresses
    def all_tracked_tokens(self) -> dict[str, list[int]]: ...           # address -> chat_ids
    # stats
    def record_buy(self, chat_id: int, buy: "BuyEvent") -> None: ...
    def get_stats_24h(self, chat_id: int, token: str | None = None) -> dict: ...
        # -> {"count": int, "volume_mon": float, "volume_usd": float}
    def get_top_buyers(self, chat_id: int, token: str | None = None, limit: int = 10) -> list[tuple[str, float]]: ...
        # -> [(buyer, total_mon), ...] sorted desc
```

### 5.4 `core/i18n.py`

```python
SUPPORTED_LANGS = {"en": "English", "es": "Español", "zh": "中文"}

def t(lang: str, key: str, **kwargs) -> str:
    """Translate key into lang (fallback: en). Formats with kwargs.
    Missing key -> returns key itself."""
```

Every user-facing string lives in `locales/*.json` (nested dicts allowed, dot-keys e.g. `"buy.title"`). All three files must contain the **identical key set**. Required keys (minimum): `welcome`, `help`, `token.added`, `token.exists`, `token.removed`, `token.not_found`, `token.list_empty`, `token.list_header`, `settings.updated`, `settings.show`, `language.set`, `language.invalid`, `emoji.set`, `minbuy.set`, `whale.set`, `buy.title`, `buy.spent`, `buy.received`, `buy.buyer`, `buy.price`, `buy.mcap`, `buy.incubation`, `buy.whale`, `price.line`, `error.invalid_address`, `error.admin_only`, `error.generic`, `incubation.title`, `incubation.progress`, `incubation.graduated`, `incubation.not_incubating`, `stats.title`, `stats.line`, `leaderboard.title`, `leaderboard.row`, `leaderboard.empty`, `about`.

### 5.5 `chain/client.py`

```python
from web3 import AsyncWeb3

def get_w3() -> AsyncWeb3:
    """Returns connected AsyncWeb3 (async HTTP provider). Raises on failure."""
```

### 5.6 `chain/price.py`

```python
async def get_token_info(address: str) -> TokenInfo: ...        # ERC20 metadata via RPC
async def get_price_mon(address: str) -> float: ...             # 0.0 if no liquidity found
async def get_mcap_mon(address: str) -> float: ...              # price * total_supply, 0.0 if unknown
```
Price strategy: find best WMON liquidity — try curve (`Sync` reserves) then UniV2-style pairs (getReserves); if none found return 0.0. Never raise on missing liquidity.

### 5.7 `chain/incubation.py`

```python
async def get_curve_info(token_address: str) -> CurveInfo: ...
```
Detects whether the token is on a bonding curve (probe curve contract / recent Transfer senders that are curve-like contracts). `is_incubating=True` while trading on curve; `graduated=True` after DEX migration. Unknown values → None. Never raise.

### 5.8 `chain/detector.py`

```python
async def build_buy_event(w3, transfer_log, token_info: TokenInfo) -> BuyEvent | None:
    """Given an ERC20 Transfer log of the tracked token, decide if it is a BUY.
    Buy = tokens moved FROM a contract (pair/pool/curve) TO an EOA/contract buyer,
    AND the same tx contains a UniV2 Swap / UniV3 Swap / curve Buy event.
    Returns None for sells, plain wallet transfers, mints to pair, etc."""
```
- UniV2 `Swap(address,uint256,uint256,uint256,uint256)` topic: `0xd78ad95f...` (compute with `w3.keccak(text=...)`).
- UniV3 `Swap(address,address,int256,int256,uint160,uint128,int24)`.
- Curve `Buy(address,address,uint256,uint256)`.
- MON spent: WMON side of the swap amounts; curve: `amountIn`; fallback `tx.value`.

### 5.9 `chain/listener.py`

```python
class BuyListener:
    def __init__(self, on_buy) -> None:
        """on_buy: async callable (chat_ids: list[int], buy: BuyEvent) -> None"""
    async def add_token(self, address: str) -> None: ...
    async def remove_token(self, address: str) -> None: ...
    async def start(self) -> None: ...   # infinite polling loop (eth_getLogs, Transfer topic, batch token addresses)
    async def stop(self) -> None: ...
```
Polls `Config.BLOCKS_PER_POLL` blocks every `Config.POLL_INTERVAL`s; dedupes tx_hash+logIndex; calls `detector.build_buy_event` per candidate Transfer; resolves chat_ids from a shared callback set by `bot/main.py` (expose `set_chat_resolver(fn)` where `fn(address) -> list[int]`).

### 5.10 `bot/notifier.py`

```python
async def send_buy_alert(bot, chat_id: int, settings: GroupSettings, buy: BuyEvent, curve: CurveInfo | None) -> None: ...
```
- Skips if `buy.amount_mon < settings.min_buy_mon` (unless amount_mon == 0.0 unknown → still send).
- Whale if `amount_mon >= settings.whale_mon` → use `whale_emoji`, else `buy_emoji`.
- Emoji repeat count: `min(20, max(1, int(amount_mon / settings.emoji_step_mon)))`.
- Fully translated via `i18n.t(settings.language, ...)`.
- Inline keyboard from `bot/keyboards.py`: `[Tx]` explorer, `[Chart]`, `[Buy]` (BUY_URL_TEMPLATE with {token}).

### 5.11 Commands (all English names + descriptions; registered via `set_my_commands`)

| Command | Description | Admin |
|---|---|---|
| /start | Welcome message and bot intro | no |
| /help | Show all commands | no |
| /addtoken `<address>` | Track a token's buys in this group | yes |
| /removetoken `<address>` | Stop tracking a token | yes |
| /tokens | List tracked tokens | no |
| /setemoji `<emoji>` | Set custom buy alert emoji | yes |
| /setwhaleemoji `<emoji>` | Set custom whale alert emoji | yes |
| /setlanguage `<en|es|zh>` | Set group language | yes |
| /setminbuy `<MON>` | Minimum buy amount to trigger alerts | yes |
| /setwhale `<MON>` | Whale alert threshold in MON | yes |
| /price `[address]` | Token price in MON/USD | no |
| /mcap `[address]` | Token market cap | no |
| /incubation `[address]` | Bonding-curve (incubation) progress | no |
| /stats | 24h buy stats for this group | no |
| /leaderboard | Top buyers in this group | no |
| /settings | Show current group settings | no |
| /about | About this bot | no |

Admin check: only group admins can use admin commands (in private chats everyone is allowed). `[address]` optional → uses the group's first tracked token.

## 6. Alert Message Format (rendered through i18n)

```
{emoji×n} {buy.title} | {token_name} (${symbol}) {emoji×n}
{buy.spent}: 1,234.56 MON ($789.00)
{buy.received}: 10,000,000 SYMBOL
{buy.buyer}: 0xabc…def
{buy.price}: 0.00000123 MON
{buy.incubation}: 45%  ← only when CurveInfo.is_incubating
{buy.whale}  ← whale line only when whale
```

## 7. Non-Functional

- No live RPC in tests — mock `web3` and `httpx`.
- `python main.py` must start cleanly with a `.env` containing only `TELEGRAM_TOKEN` (all other fields defaulted).
- Graceful reconnect on RPC errors (log + backoff, never crash the loop).
- `.gitignore`: `.env`, `data/`, `__pycache__/`, `*.pyc`.
- README.md (EN) + README.es.md (ES): features, commands table, setup (BotFather, .env, docker), Monad notes.

## 8. Ownership (worktree branches)

- **Coder A** — branch `feature/core`: `core/`, `locales/`, `tests/test_i18n.py`, `tests/test_db.py`
- **Coder B** — branch `feature/chain`: `chain/`, `tests/test_detector.py`
- **Coder C** — branch `feature/bot`: `bot/`, `main.py`, `tests/test_notifier.py`, `Dockerfile`, `docker-compose.yml`, `.env.example`, `.gitignore`, `requirements.txt`, `README.md`, `README.es.md`

Shared `requirements.txt` (owned by C; A and B may use: `web3>=7`, `python-telegram-bot>=21`, `python-dotenv`, `pytest`, `pytest-asyncio` — all already in requirements).
