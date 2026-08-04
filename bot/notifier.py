"""Buy/sell alert message formatting and sending (SPEC 5.10 + SPEC-v2 §7)."""

from __future__ import annotations

import logging
from typing import Optional

from telegram import MessageEntity

from core.config import Config, load_config
from core.i18n import t
from core.models import BuyEvent, CurveInfo, GroupSettings, SellEvent

from bot import emojis
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


def _fmt_price(value: float) -> str:
    """Unit price: fixed 2 decimals for normal prices; tiny prices
    (< 0.01) keep their trimmed precision so they don't print as "0.00"."""
    if value == 0:
        return "0"
    if abs(value) >= 0.01:
        return f"{value:,.2f}"
    return _fmt_num(value)


def _short_addr(address: str) -> str:
    if len(address) > 13:
        return f"{address[:6]}…{address[-4:]}"
    return address


def _emoji_line(emoji_value: str, count: int, prefix: str) -> "tuple[str, list]":
    """Emoji line text + custom-emoji entities for it.

    ``prefix`` is the message text preceding the emoji line (title + "\\n"),
    used to compute UTF-16 entity offsets. Regular Unicode emojis produce
    no entities; an encoded custom emoji (``tg:<id>:<fallback>``) renders
    the fallback ``count`` times with one custom_emoji entity each.
    """
    decoded = emojis.decode_custom_emoji(emoji_value)
    if decoded is None:
        return emoji_value * count, []
    custom_id, fallback = decoded
    start = emojis.utf16_len(prefix)
    unit = emojis.utf16_len(fallback)
    entities = [
        MessageEntity(
            type=MessageEntity.CUSTOM_EMOJI,
            offset=start + i * unit,
            length=unit,
            custom_emoji_id=custom_id,
        )
        for i in range(count)
    ]
    return fallback * count, entities


def buy_value_usd(
    amount_mon: float, amount_usd: "float | None", mon_usd: float
) -> float:
    """USD value of a buy/sell (SPEC-v3 §4).

    ``amount_usd`` when present; otherwise ``amount_mon * mon_usd``; 0.0
    when there is no price feed (callers fall back to v2 MON behaviour).
    """
    if amount_usd is not None and amount_usd > 0:
        return float(amount_usd)
    if amount_mon and mon_usd and mon_usd > 0:
        return float(amount_mon) * float(mon_usd)
    return 0.0


async def send_buy_alert(
    bot,
    chat_id: int,
    settings: GroupSettings,
    buy: BuyEvent,
    curve: CurveInfo | None,
    mon_usd: float = 0.0,
    mcap_mon: float = 0.0,
    is_new_buyer: bool = False,
) -> None:
    """Format and send a buy alert to a chat (SPEC-v3 §4: USDT thresholds).

    - The min-buy filter, whale threshold and emoji repetition all compare
      against the buy's USD value (``buy_value_usd``) and the
      ``settings.*_usdt`` fields.
    - When the USD value is 0.0 (no price feed) the v2 behaviour applies:
      the alert is sent anyway, with no whale line and a single emoji.
    - The spent line shows USDT first (MON in parentheses); MON only when
      there is no USD value.
    - The price line shows USDT first (MON in parentheses) when a MON/USD
      rate is available; MON only otherwise.
    - ``mcap_mon`` (token market cap in MON, 0.0 = unknown) adds a market-cap
      line, in USDT when a MON/USD rate is available.
    - ``is_new_buyer`` adds a "new buyer" line (first buy of this token in
      this group).
    - ``mon_usd``, ``mcap_mon`` and ``is_new_buyer`` are optional kwargs:
      v2 callers (no kwargs) keep working.
    - Incubation line only when curve.is_incubating.
    """
    lang = settings.language
    usd = buy_value_usd(buy.amount_mon, buy.amount_usd, mon_usd)

    # min-buy filter in USDT (usd == 0.0 == no feed -> v2: always alert)
    if usd > 0.0 and usd < settings.min_buy_usdt:
        return

    is_whale = usd > 0.0 and usd >= settings.whale_usdt
    emoji = settings.whale_emoji if is_whale else settings.buy_emoji

    if usd > 0.0:
        step = settings.emoji_step_usdt if settings.emoji_step_usdt > 0 else 1.0
        count = min(20, max(1, int(usd / step)))
    else:
        count = 1

    title = f"{t(lang, 'buy.title')} | {buy.token_name} (${buy.token_symbol})"
    emoji_text, entities = _emoji_line(emoji, count, title + "\n")

    lines = [
        title,
        emoji_text,
    ]

    if usd > 0.0:
        lines.append(
            t(lang, "buy.spent", usd=f"{usd:,.2f}", mon=_fmt_num(buy.amount_mon))
        )
    else:
        lines.append(t(lang, "buy.spent_mon", mon=_fmt_num(buy.amount_mon)))

    lines.append(
        f"{t(lang, 'buy.received')}: {_fmt_num(buy.amount_token)} {buy.token_symbol}"
    )
    lines.append(f"{t(lang, 'buy.buyer')}: {_short_addr(buy.buyer)}")
    if is_new_buyer:
        lines.append(t(lang, "buy.new_buyer"))

    if usd > 0.0 and mon_usd > 0 and buy.price_mon > 0:
        price_usd = buy.price_mon * mon_usd
        lines.append(
            f"{t(lang, 'buy.price')}: {_fmt_price(price_usd)} USDT "
            f"(≈ {_fmt_price(buy.price_mon)} MON)"
        )
    else:
        lines.append(f"{t(lang, 'buy.price')}: {_fmt_price(buy.price_mon)} MON")

    if mcap_mon > 0:
        if mon_usd > 0:
            lines.append(
                t(
                    lang,
                    "buy.mcap",
                    mcap_usd=_fmt_num(mcap_mon * mon_usd),
                    mcap_mon=_fmt_num(mcap_mon),
                )
            )
        else:
            lines.append(t(lang, "buy.mcap_mon", mcap_mon=_fmt_num(mcap_mon)))

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
            entities=entities or None,
        )
    except Exception:
        logger.exception("failed to send buy alert to chat %s", chat_id)


async def send_sell_alert(
    bot,
    chat_id: int,
    settings: GroupSettings,
    sell: SellEvent,
    curve: CurveInfo | None,
    mon_usd: float = 0.0,
) -> None:
    """Format and send a sell alert (SPEC-v2 §7 + SPEC-v3 §4).

    Same layout as the buy alert but titled with ``sell.title`` and using
    ``settings.sell_emoji``; ``sell.buyer`` is the seller and
    ``sell.amount_mon`` the MON received. The min-buy threshold is applied
    in USDT against ``buy_value_usd`` (usd == 0.0 -> v2: still sent, one
    emoji). The received line shows USDT first (MON in parentheses).
    ``mon_usd`` is an optional kwarg: v2 callers keep working.
    """
    lang = settings.language
    usd = buy_value_usd(sell.amount_mon, sell.amount_usd, mon_usd)

    # min threshold filter in USDT (usd == 0.0 == no feed -> always alert)
    if usd > 0.0 and usd < settings.min_buy_usdt:
        return

    if usd > 0.0:
        step = settings.emoji_step_usdt if settings.emoji_step_usdt > 0 else 1.0
        count = min(20, max(1, int(usd / step)))
    else:
        count = 1

    title = f"{t(lang, 'sell.title')} | {sell.token_name} (${sell.token_symbol})"
    emoji_text, entities = _emoji_line(settings.sell_emoji, count, title + "\n")

    lines = [
        title,
        emoji_text,
    ]

    if usd > 0.0:
        lines.append(
            t(lang, "sell.spent", usd=f"{usd:,.2f}", mon=_fmt_num(sell.amount_mon))
        )
    else:
        lines.append(t(lang, "sell.spent_mon", mon=_fmt_num(sell.amount_mon)))

    lines.append(
        f"{t(lang, 'sell.sold')}: {_fmt_num(sell.amount_token)} {sell.token_symbol}"
    )
    lines.append(f"{t(lang, 'sell.seller')}: {_short_addr(sell.buyer)}")
    lines.append(f"{t(lang, 'buy.price')}: {_fmt_price(sell.price_mon)} MON")

    if curve is not None and curve.is_incubating:
        pct = f"{curve.progress_pct:.0f}%" if curve.progress_pct is not None else "?%"
        lines.append(f"{t(lang, 'buy.incubation')}: {pct}")

    text = "\n".join(lines)
    keyboard = buy_alert_keyboard(sell, _get_config())

    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            disable_web_page_preview=True,
            entities=entities or None,
        )
    except Exception:
        logger.exception("failed to send sell alert to chat %s", chat_id)
