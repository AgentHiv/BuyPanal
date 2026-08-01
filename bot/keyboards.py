"""Inline keyboards for buy alert messages and button-based configuration.

Alert buttons: [Tx] -> explorer transaction page, [Chart] -> explorer token
page, [Buy] -> BUY_URL_TEMPLATE with the {token} placeholder replaced by the
token address.

Configuration keyboards (SPEC-v2 §8.1) drive the ``cfg:*`` callback menu
system implemented in ``bot/callbacks.py``.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from core.config import Config
from core.i18n import SUPPORTED_LANGS, t
from core.models import BuyEvent, GroupSettings


def buy_alert_keyboard(buy: BuyEvent, config: Config) -> InlineKeyboardMarkup:
    """Build the [Tx][Chart][Buy] inline keyboard for a buy alert."""
    tx_url = f"{config.EXPLORER_URL.rstrip('/')}/tx/{buy.tx_hash}"
    chart_url = f"{config.EXPLORER_URL.rstrip('/')}/token/{buy.token_address}"
    template = config.BUY_URL_TEMPLATE or "https://nad.fun/token/{token}"
    if "{token}" in template:
        buy_url = template.replace("{token}", buy.token_address)
    else:
        buy_url = template
    buttons = [
        InlineKeyboardButton("Tx", url=tx_url),
        InlineKeyboardButton("Chart", url=chart_url),
        InlineKeyboardButton("Buy", url=buy_url),
    ]
    return InlineKeyboardMarkup([buttons])


# ---------------------------------------------------------------------------
# SPEC-v2 §8.1 — configuration menus
# ---------------------------------------------------------------------------

# Emoji presets (at least 10 per kind).
EMOJI_PRESETS: dict[str, list[str]] = {
    "buy": ["🟢", "🟩", "💚", "🚀", "🔥", "💰", "🤑", "⚡", "🦄", "🌕"],
    "whale": ["🐋", "🐳", "🦈", "🐙", "💎", "🏦", "👑", "🔱", "⚓", "🌊"],
    "sell": ["🔴", "🟥", "❤️", "💔", "📉", "🩸", "🚨", "⬇️", "🍅", "👹"],
}

# Amount presets (MON) for min_buy_mon / whale_mon / emoji_step_mon.
AMOUNT_PRESETS: list[float] = [1, 5, 10, 50, 100]

# emoji kind -> GroupSettings attribute
EMOJI_KIND_TO_ATTR = {"buy": "buy_emoji", "whale": "whale_emoji", "sell": "sell_emoji"}

# amount field -> GroupSettings attribute (identity, kept for clarity)
AMOUNT_FIELDS = ("min_buy_mon", "whale_mon", "emoji_step_mon")

# toggle field -> GroupSettings attribute
TOGGLE_FIELDS = ("sell_alerts", "scanner_alerts")

_DEFAULTS = {"sell_emoji": "🔴", "sell_alerts": False, "scanner_alerts": False}


def _setting(settings: GroupSettings, name: str):
    """getattr with v2 defaults so v1 GroupSettings objects keep working."""
    return getattr(settings, name, _DEFAULTS.get(name))


def _back_button(lang: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(t(lang, "ui.btn_back"), callback_data="cfg:back")


def _fmt_amount(value: float) -> str:
    return f"{value:g}" if isinstance(value, (int, float)) else str(value)


def build_settings_keyboard(settings: GroupSettings, lang: str) -> InlineKeyboardMarkup:
    """Main settings menu: language, emojis, amounts, tokens and toggles."""
    on_off = lambda v: t(lang, "ui.on") if v else t(lang, "ui.off")  # noqa: E731
    rows = [
        [
            InlineKeyboardButton(
                t(lang, "ui.btn_language", value=settings.language),
                callback_data="cfg:lang",
            )
        ],
        [
            InlineKeyboardButton(
                t(lang, "ui.btn_emoji_buy", value=settings.buy_emoji),
                callback_data="cfg:emoji:buy",
            )
        ],
        [
            InlineKeyboardButton(
                t(lang, "ui.btn_emoji_whale", value=settings.whale_emoji),
                callback_data="cfg:emoji:whale",
            )
        ],
        [
            InlineKeyboardButton(
                t(lang, "ui.btn_emoji_sell", value=_setting(settings, "sell_emoji")),
                callback_data="cfg:emoji:sell",
            )
        ],
        [
            InlineKeyboardButton(
                t(lang, "ui.btn_minbuy", value=_fmt_amount(settings.min_buy_mon)),
                callback_data="cfg:amount:min_buy_mon",
            )
        ],
        [
            InlineKeyboardButton(
                t(lang, "ui.btn_whale", value=_fmt_amount(settings.whale_mon)),
                callback_data="cfg:amount:whale_mon",
            )
        ],
        [
            InlineKeyboardButton(
                t(lang, "ui.btn_emojistep", value=_fmt_amount(settings.emoji_step_mon)),
                callback_data="cfg:amount:emoji_step_mon",
            )
        ],
        [
            InlineKeyboardButton(
                t(lang, "ui.btn_toggle_sells", value=on_off(_setting(settings, "sell_alerts"))),
                callback_data="cfg:toggle:sell_alerts",
            )
        ],
        [
            InlineKeyboardButton(
                t(lang, "ui.btn_toggle_scanner", value=on_off(_setting(settings, "scanner_alerts"))),
                callback_data="cfg:toggle:scanner_alerts",
            )
        ],
        [InlineKeyboardButton(t(lang, "ui.btn_tokens"), callback_data="cfg:tokens")],
        [InlineKeyboardButton(t(lang, "ui.btn_close"), callback_data="cfg:close")],
    ]
    return InlineKeyboardMarkup(rows)


def build_language_keyboard(current: str) -> InlineKeyboardMarkup:
    """Language picker; the current language is marked with ✅."""
    rows = []
    for code, name in SUPPORTED_LANGS.items():
        label = f"✅ {name}" if code == current else name
        rows.append([InlineKeyboardButton(label, callback_data=f"cfg:lang:set:{code}")])
    rows.append([_back_button(current)])
    return InlineKeyboardMarkup(rows)


def build_emoji_preset_keyboard(kind: str) -> InlineKeyboardMarkup:
    """Emoji preset grid for ``kind`` ("buy" | "whale" | "sell") + custom."""
    presets = EMOJI_PRESETS.get(kind, EMOJI_PRESETS["buy"])
    rows = []
    for i in range(0, len(presets), 5):
        rows.append(
            [
                InlineKeyboardButton(e, callback_data=f"cfg:emoji:{kind}:set:{e}")
                for e in presets[i : i + 5]
            ]
        )
    rows.append(
        [InlineKeyboardButton(t("en", "ui.btn_custom"), callback_data=f"cfg:emoji:{kind}:custom")]
    )
    rows.append([_back_button("en")])
    return InlineKeyboardMarkup(rows)


def build_amount_keyboard(field: str) -> InlineKeyboardMarkup:
    """Amount presets (1/5/10/50/100 MON) + custom for a numeric field."""
    rows = [
        [
            InlineKeyboardButton(
                f"{v:g}", callback_data=f"cfg:amount:{field}:set:{v:g}"
            )
            for v in AMOUNT_PRESETS
        ]
    ]
    rows.append(
        [InlineKeyboardButton(t("en", "ui.btn_custom"), callback_data=f"cfg:amount:{field}:custom")]
    )
    rows.append([_back_button("en")])
    return InlineKeyboardMarkup(rows)


def _short(address: str) -> str:
    return f"{address[:6]}…{address[-4:]}" if len(address) > 13 else address


def build_tokens_keyboard(tokens: list[str], lang: str) -> InlineKeyboardMarkup:
    """One 🗑 button per tracked token."""
    rows = [
        [
            InlineKeyboardButton(
                f"🗑 {_short(addr)}", callback_data=f"cfg:token:del:{addr}"
            )
        ]
        for addr in tokens
    ]
    rows.append([_back_button(lang)])
    return InlineKeyboardMarkup(rows)


# Username used by the "Add me to your group" URL button. Set once at
# startup via set_bot_username() (bot/main.py post_init).
_bot_username: str | None = None


def set_bot_username(username: str | None) -> None:
    global _bot_username
    _bot_username = username


def build_start_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Welcome keyboard: ⚙️ Settings · 📖 Help · ➕ Add me to your group."""
    rows = [
        [
            InlineKeyboardButton(t(lang, "ui.btn_settings"), callback_data="cfg:menu"),
            InlineKeyboardButton(t(lang, "ui.btn_help"), callback_data="cfg:help"),
        ]
    ]
    if _bot_username:
        rows.append(
            [
                InlineKeyboardButton(
                    t(lang, "ui.add_to_group"),
                    url=f"https://t.me/{_bot_username}?startgroup=true",
                )
            ]
        )
    return InlineKeyboardMarkup(rows)
