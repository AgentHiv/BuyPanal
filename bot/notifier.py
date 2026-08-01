"""Buy alert message formatting and sending (SPEC 5.10 / section 6)."""

from __future__ import annotations

import logging
from typing import Optional

from core.config import Config, load_config
from core.i18n import t
from core.models import BuyEvent, CurveInfo, GroupSettings

from bot.keyboards import buy_alert_keyboard

logger = logging.getLogger(__name__)

_config: Optional[Config] = None


def set_config(config: Config) -> None:
    """Allow bot/main.py to inject the already-loaded Config."""
    global _config
    _config = config


def _get_config() -> Config:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def _fmt_num(value: float) -> str:
    """Human-friendly number: thousands separators, trimmed decimals."""
    if value == 0:
        return "0"
    if abs(value) >= 1:
        return f"{value:,.2f}"
    # small values: keep up to 10 decimals, trim trailing zeros
    text = f"{value:,.10f}".rstrip("0").rstrip(".")
    return text


def _short_addr(address: str) -> str:
    if len(address) > 13:
        return f"{address[:6]}…{address[-4:]}"
    return address


async def send_buy_alert(
    bot,
    chat_id: int,
    settings: GroupSettings,
    buy: BuyEvent,
    curve: CurveInfo | None,
) -> None:
    """Format and send a buy alert to a chat.

    - Skips when buy.amount_mon < settings.min_buy_mon
      (amount_mon == 0.0 means "unknown" -> still sent).
    - Whale when amount_mon >= settings.whale_mon -> whale_emoji.
    - Emoji repeat: min(20, max(1, int(amount_mon / emoji_step_mon))).
    - Incubation line only when curve.is_incubating.
    """
    lang = settings.language

    # min-buy filter (0.0 == unknown amount -> always alert)
    if buy.amount_mon != 0.0 and buy.amount_mon < settings.min_buy_mon:
        return

    is_whale = buy.amount_mon >= settings.whale_mon
    emoji = settings.whale_emoji if is_whale else settings.buy_emoji

    step = settings.emoji_step_mon if settings.emoji_step_mon > 0 else 1.0
    count = min(20, max(1, int(buy.amount_mon / step)))
    emojis = emoji * count

    lines = [
        f"{emojis} {t(lang, 'buy.title')} | "
        f"{buy.token_name} (${buy.token_symbol}) {emojis}"
    ]

    spent = f"{_fmt_num(buy.amount_mon)} MON"
    if buy.amount_usd is not None:
        spent += f" (${buy.amount_usd:,.2f})"
    lines.append(f"{t(lang, 'buy.spent')}: {spent}")

    lines.append(
        f"{t(lang, 'buy.received')}: {_fmt_num(buy.amount_token)} {buy.token_symbol}"
    )
    lines.append(f"{t(lang, 'buy.buyer')}: {_short_addr(buy.buyer)}")
    lines.append(f"{t(lang, 'buy.price')}: {_fmt_num(buy.price_mon)} MON")

    if curve is not None and curve.is_incubating:
        pct = f"{curve.progress_pct:.0f}%" if curve.progress_pct is not None else "?%"
        lines.append(f"{t(lang, 'buy.incubation')}: {pct}")

    if is_whale:
        lines.append(t(lang, "buy.whale"))

    text = "\n".join(lines)
    keyboard = buy_alert_keyboard(buy, _get_config())

    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
    except Exception:
        logger.exception("failed to send buy alert to chat %s", chat_id)
