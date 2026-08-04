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

from bot import emojis


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

# Amount presets in USDT (SPEC-v3 §5.2) for the *_usdt threshold fields.
AMOUNT_PRESETS: list[float] = [5, 25, 100, 500, 1000]

# emoji kind -> GroupSettings attribute
EMOJI_KIND_TO_ATTR = {"buy": "buy_emoji", "whale": "whale_emoji", "sell": "sell_emoji"}

# amount field -> GroupSettings attribute (SPEC-v3: USDT thresholds only;
# the legacy *_mon fields stay in the schema but are not edited by the UI)
AMOUNT_FIELDS = ("min_buy_usdt", "whale_usdt", "emoji_step_usdt")

# amount field -> i18n label key (SPEC-v3 §5.2)
AMOUNT_FIELD_LABELS = {
    "min_buy_usdt": "ui.btn_minbuy_usdt",
    "whale_usdt": "ui.btn_whale_usdt",
    "emoji_step_usdt": "ui.btn_emojistep_usdt",
}

# toggle field -> GroupSettings attribute
TOGGLE_FIELDS = ("sell_alerts", "scanner_alerts")

_DEFAULTS = {
    "sell_emoji": "🔴",
    "sell_alerts": False,
    "scanner_alerts": False,
    "min_buy_usdt": 5.0,
    "whale_usdt": 500.0,
    "emoji_step_usdt": 25.0,
}


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
                t(lang, "ui.btn_emoji_buy", value=emojis.display_emoji(settings.buy_emoji)),
                callback_data="cfg:emoji:buy",
            )
        ],
        [
            InlineKeyboardButton(
                t(lang, "ui.btn_emoji_whale", value=emojis.display_emoji(settings.whale_emoji)),
                callback_data="cfg:emoji:whale",
            )
        ],
        [
            InlineKeyboardButton(
                t(lang, "ui.btn_emoji_sell", value=emojis.display_emoji(_setting(settings, "sell_emoji"))),
                callback_data="cfg:emoji:sell",
            )
        ],
        [
            InlineKeyboardButton(
                t(lang, "ui.btn_minbuy_usdt", value=_fmt_amount(_setting(settings, "min_buy_usdt"))),
                callback_data="cfg:amount:min_buy_usdt",
            )
        ],
        [
            InlineKeyboardButton(
                t(lang, "ui.btn_whale_usdt", value=_fmt_amount(_setting(settings, "whale_usdt"))),
                callback_data="cfg:amount:whale_usdt",
            )
        ],
        [
            InlineKeyboardButton(
                t(lang, "ui.btn_emojistep_usdt", value=_fmt_amount(_setting(settings, "emoji_step_usdt"))),
                callback_data="cfg:amount:emoji_step_usdt",
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
    """Amount presets (5/25/100/500/1000 USDT) + custom for a numeric field."""
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
    """Welcome keyboard: ⚙️ Settings · 📖 Help · ➕ Track token · Add me."""
    rows = [
        [
            InlineKeyboardButton(t(lang, "ui.btn_settings"), callback_data="cfg:menu"),
            InlineKeyboardButton(t(lang, "ui.btn_help"), callback_data="cfg:help"),
        ],
        [
            InlineKeyboardButton(t(lang, "ui.btn_setup"), callback_data="cfg:setup"),
        ],
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


# ---------------------------------------------------------------------------
# SPEC-v3 §5.1 — guided token-setup wizard
# ---------------------------------------------------------------------------


def build_wizard_card_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Token card actions: ✅ Start tracking · ❌ Cancel."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t(lang, "wizard.btn_track"), callback_data="cfg:wizard:track"
                ),
                InlineKeyboardButton(
                    t(lang, "wizard.btn_cancel"), callback_data="cfg:wizard:cancel"
                ),
            ]
        ]
    )


def build_wizard_quick_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Quick-config menu shown right after a token is tracked.

    Each button opens the existing v2 sub-menu (cfg:* callbacks); ✔️ Done
    shows the final settings summary.
    """
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t(lang, "wizard.btn_emoji"), callback_data="cfg:emoji:buy"
                ),
                InlineKeyboardButton(
                    t(lang, "wizard.btn_minbuy"), callback_data="cfg:amount:min_buy_usdt"
                ),
            ],
            [
                InlineKeyboardButton(
                    t(lang, "wizard.btn_whale"), callback_data="cfg:amount:whale_usdt"
                ),
                InlineKeyboardButton(
                    t(lang, "wizard.btn_language"), callback_data="cfg:lang"
                ),
            ],
            [
                InlineKeyboardButton(
                    t(lang, "wizard.btn_done"), callback_data="cfg:wizard:done"
                ),
            ],
        ]
    )
