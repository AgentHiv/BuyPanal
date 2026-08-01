# Monad Buy Bot

A Telegram buy-alert bot for the **Monad** blockchain. Add it to your group,
track any ERC-20 token, and get real-time buy alerts with customizable emojis,
whale alerts and 24h stats — for both DEX-listed tokens and bonding-curve
("incubation") tokens.

## Features

- **Any token on Monad** — DEX-listed (PancakeSwap v2/v3, Uniswap v3, Kuru)
  and **incubation / bonding-curve tokens** (nad.fun-style launchpads), with
  live graduation progress in the alert.
- **Customizable emojis** — per-group buy emoji and whale emoji; the alert
  repeats 1 emoji per `N` MON spent (configurable step, capped at 20).
- **Whale alerts** — dedicated emoji and whale line above a per-group MON
  threshold.
- **Multi-language** — English (`en`), Español (`es`), 中文 (`zh`) per group.
- **Stats & leaderboard** — 24h buy volume and top buyers per group.
- **Inline buttons** — every alert links to the transaction, the chart and a
  buy page.
- **Button-based configuration** — `/settings` opens an interactive inline
  menu: language, emojis (with presets), thresholds, sell/scanner toggles
  and token management, no commands to memorize.
- **SQLite persistence** — settings, tracked tokens and stats survive restarts.

## Monad notes

- Mainnet: **chain ID 143**, RPC `https://rpc.monad.xyz`, native token **MON**
  (18 decimals), explorer `https://monadvision.com`.
- Testnet: chain ID 10143, RPC `https://testnet-rpc.monad.xyz`.
- Monad is fully EVM-compatible; the bot uses standard `web3.py` tooling.
- Incubation tokens trade on a bonding curve until graduation (~225,000 MON
  raised), after which liquidity moves to a DEX. Both phases are tracked.

## Commands

| Command | Description | Admin |
|---|---|---|
| `/start` | Welcome message and bot intro | no |
| `/help` | Show all commands | no |
| `/addtoken <address>` | Track a token's buys in this group | yes |
| `/removetoken <address>` | Stop tracking a token | yes |
| `/tokens` | List tracked tokens | no |
| `/setemoji <emoji>` | Set custom buy alert emoji | yes |
| `/setwhaleemoji <emoji>` | Set custom whale alert emoji | yes |
| `/setlanguage <en\|es\|zh>` | Set group language | yes |
| `/setminbuy <MON>` | Minimum buy amount to trigger alerts | yes |
| `/setwhale <MON>` | Whale alert threshold in MON | yes |
| `/price [address]` | Token price in MON/USD | no |
| `/mcap [address]` | Token market cap | no |
| `/incubation [address]` | Bonding-curve (incubation) progress | no |
| `/stats` | 24h buy stats for this group | no |
| `/leaderboard` | Top buyers in this group | no |
| `/settings` | Open the button-based settings menu | no |
| `/about` | About this bot | no |

Admin commands require group administrator rights; in private chats everyone
is allowed. `[address]` is optional and defaults to the group's first tracked
token.

## Button-based configuration

Send `/settings` in your group and configure everything with inline buttons:

- **Language** — picker for English / Español / 中文 (current one marked ✅).
- **Emojis** — buy, whale and sell alert emojis. Pick from 10+ presets per
  type (🟢🟩💚🚀🔥💰🤑⚡🦄🌕 for buys, 🐋🐳🦈🐙💎🏦👑🔱⚓🌊 for whales) or tap
  **✏️ Custom** and reply with your own emoji (guided input via ForceReply).
- **Amounts** — min buy, whale threshold and emoji step, with 1/5/10/50/100
  MON presets or a custom value (validated, must be > 0).
- **Toggles** — sell alerts and new-token (scanner) alerts, ON/OFF per group.
- **Tokens** — the tracked-token list with a 🗑 button per token to stop
  tracking it. `/tokens` shows the same buttons.
- `/start` in a group shows ⚙️ Settings / 📖 Help / ➕ Add me shortcuts.

Anyone in the group can open and browse the menus; only group admins can
change values (same rule as the admin commands).

## Advanced tools

When the advanced modules are enabled, these extra commands become
available (price alerts, sell alerts, launch scanner, token security card
and a live dashboard):

| Command | Description | Admin |
|---|---|---|
| `/pricealert <address> <above\|below> <price_MON>` | Set a price alert for a token | no |
| `/alerts` | List and manage your price alerts | no |
| `/scanner` | Toggle new incubation-token launch alerts | yes |
| `/sells` | Toggle sell alerts in this group | yes |
| `/tokeninfo [address]` | Token security card (liquidity, holders) | no |
| `/dashboard` | Post a live auto-updating stats dashboard | yes |

Sell alerts and scanner alerts can also be toggled from the `/settings`
button menu.

## Setup

### 1. Create the bot with BotFather

1. Open [@BotFather](https://t.me/BotFather) in Telegram.
2. Send `/newbot` and follow the prompts to get your **bot token**.
3. (Recommended) Send `/setprivacy` → `Disable` so the bot can read group
   messages, then add the bot to your group and make it a member.

### 2. Configure the environment

```bash
cp .env.example .env
# edit .env and set TELEGRAM_TOKEN
```

Only `TELEGRAM_TOKEN` is required — every other field has a working default
(Monad mainnet RPC, chain ID 143, monadvision explorer, nad.fun buy links).
See `.env.example` for the full list.

### 3a. Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

### 3b. Run with Docker

```bash
docker build -t monad-buy-bot .
docker run -d --env-file .env -v $(pwd)/data:/app/data monad-buy-bot
```

or with docker compose:

```bash
docker compose up -d --build
```

The `data/` volume persists the SQLite database across restarts.

### 4. Start tracking

In your group (as an admin):

```
/addtoken 0x1234...abcd
/setminbuy 5
/setwhale 500
/setemoji 🔥
/setlanguage en
```

## Tests

```bash
pip install -r requirements.txt
python -m pytest tests/ -v
```

Tests are fully mocked — no live RPC or Telegram calls.

## Project layout

```
bot/        Telegram layer (handlers, notifier, keyboards, app builder)
chain/      Monad RPC layer (client, listener, detector, price, incubation)
core/       config, SQLite db, i18n, shared dataclasses
locales/    en.json / es.json / zh.json translations
tests/      pytest suite (mocked chain + telegram)
main.py     entrypoint
```
