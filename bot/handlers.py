"""All Telegram command handlers (SPEC 5.11).

Every user-facing string goes through core.i18n.t().
Admin commands are restricted to group administrators; in private chats
everyone is treated as an admin.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from telegram import BotCommand, Chat, Update
from telegram.constants import ChatMemberStatus
from telegram.ext import ContextTypes

from chain import incubation as chain_incubation
from chain import price as chain_price
from core.db import Database
from core.i18n import SUPPORTED_LANGS, t

from bot import keyboards

logger = logging.getLogger(__name__)

EVM_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

# Command table (English names + descriptions, registered via set_my_commands)
COMMANDS = [
    ("start", "Welcome message and bot intro", False),
    ("help", "Show all commands", False),
    ("addtoken", "Track a token's buys in this group", True),
    ("removetoken", "Stop tracking a token", True),
    ("tokens", "List tracked tokens", False),
    ("setemoji", "Set custom buy alert emoji", True),
    ("setwhaleemoji", "Set custom whale alert emoji", True),
    ("setlanguage", "Set group language (en|es|zh)", True),
    ("setup", "Guided setup: track a token in 3 taps", True),
    ("setminbuy", "Minimum buy (USDT) to trigger alerts", True),
    ("setwhale", "Whale alert threshold in USDT", True),
    ("price", "Token price in MON/USD", False),
    ("mcap", "Token market cap", False),
    ("incubation", "Bonding-curve (incubation) progress", False),
    ("stats", "24h buy stats for this group", False),
    ("leaderboard", "Top buyers in this group", False),
    ("settings", "Show current group settings", False),
    ("about", "About this bot", False),
]

BOT_COMMANDS = [BotCommand(name, desc) for name, desc, _admin in COMMANDS]


def _db(context: ContextTypes.DEFAULT_TYPE) -> Database:
    return context.application.bot_data["db"]


def _listener(context: ContextTypes.DEFAULT_TYPE):
    return context.application.bot_data.get("listener")


def _lang(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    return _db(context).get_settings(update.effective_chat.id).language


async def _is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """True in private chats; in groups only for administrators/owner."""
    chat = update.effective_chat
    user = update.effective_user
    if chat is None or user is None:
        return False
    if chat.type == Chat.PRIVATE:
        return True
    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
        return member.status in (
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        )
    except Exception:
        logger.exception("admin check failed for user %s", user.id)
        return False


def admin_only(handler):
    """Decorator: block non-admins with error.admin_only."""

    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await _is_admin(update, context):
            await update.message.reply_text(t(_lang(update, context), "error.admin_only"))
            return
        return await handler(update, context)

    return wrapper


def _resolve_token_arg(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> Optional[str]:
    """Optional [address] argument; falls back to the group's first token."""
    db = _db(context)
    chat_id = update.effective_chat.id
    if context.args:
        address = context.args[0].strip()
        if not EVM_ADDRESS_RE.match(address):
            return "__invalid__"
        return address
    tokens = db.list_tokens(chat_id)
    return tokens[0] if tokens else None


async def _reply_error(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(t(_lang(update, context), "error.generic"))


# ---------------------------------------------------------------- public cmds


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = _lang(update, context)
    chat = update.effective_chat
    if chat is not None and chat.type != Chat.PRIVATE:
        # groups: welcome + button menu (Settings / Help / Add me)
        if keyboards._bot_username is None:
            try:
                me = await context.bot.get_me()
                keyboards.set_bot_username(me.username)
            except Exception:
                logger.debug("could not resolve bot username", exc_info=True)
        await update.message.reply_text(
            f"{t(lang, 'welcome')}\n\n{t(lang, 'ui.start_buttons_help')}",
            reply_markup=keyboards.build_start_keyboard(lang),
        )
        return
    await update.message.reply_text(t(lang, "welcome"))


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(t(_lang(update, context), "help"))


async def cmd_about(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(t(_lang(update, context), "about"))


@admin_only
async def cmd_addtoken(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = _db(context)
    chat_id = update.effective_chat.id
    lang = _lang(update, context)

    if not context.args or not EVM_ADDRESS_RE.match(context.args[0].strip()):
        await update.message.reply_text(t(lang, "error.invalid_address"))
        return
    address = context.args[0].strip()

    try:
        info = await chain_price.get_token_info(address)
    except Exception:
        logger.exception("get_token_info failed for %s", address)
        await _reply_error(update, context)
        return

    if not db.add_token(chat_id, info.address, info.kind):
        await update.message.reply_text(t(lang, "token.exists"))
        return

    listener = _listener(context)
    if listener is not None:
        try:
            await listener.add_token(info.address)
        except Exception:
            logger.exception("listener.add_token failed for %s", info.address)

    await update.message.reply_text(
        t(lang, "token.added", name=info.name, symbol=info.symbol)
    )


@admin_only
async def cmd_removetoken(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = _db(context)
    chat_id = update.effective_chat.id
    lang = _lang(update, context)

    if not context.args or not EVM_ADDRESS_RE.match(context.args[0].strip()):
        await update.message.reply_text(t(lang, "error.invalid_address"))
        return
    address = context.args[0].strip()

    if not db.remove_token(chat_id, address):
        await update.message.reply_text(t(lang, "token.not_found"))
        return

    listener = _listener(context)
    if listener is not None:
        try:
            await listener.remove_token(address)
        except Exception:
            logger.exception("listener.remove_token failed for %s", address)

    await update.message.reply_text(t(lang, "token.removed"))


async def cmd_tokens(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = _db(context)
    chat_id = update.effective_chat.id
    lang = _lang(update, context)

    tokens = db.list_tokens(chat_id)
    if not tokens:
        await update.message.reply_text(t(lang, "token.list_empty"))
        return
    lines = [t(lang, "token.list_header", count=len(tokens))]
    lines += [f"`{addr}`" for addr in tokens]
    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=keyboards.build_tokens_keyboard(tokens, lang),
    )


@admin_only
async def cmd_setemoji(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = _db(context)
    chat_id = update.effective_chat.id
    lang = _lang(update, context)
    if not context.args:
        await _reply_error(update, context)
        return
    settings = db.get_settings(chat_id)
    settings.buy_emoji = context.args[0].strip()
    db.save_settings(settings)
    await update.message.reply_text(t(lang, "emoji.set", emoji=settings.buy_emoji))


@admin_only
async def cmd_setwhaleemoji(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = _db(context)
    chat_id = update.effective_chat.id
    lang = _lang(update, context)
    if not context.args:
        await _reply_error(update, context)
        return
    settings = db.get_settings(chat_id)
    settings.whale_emoji = context.args[0].strip()
    db.save_settings(settings)
    await update.message.reply_text(t(lang, "emoji.set", emoji=settings.whale_emoji))


@admin_only
async def cmd_setlanguage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = _db(context)
    chat_id = update.effective_chat.id
    lang = _lang(update, context)
    if not context.args or context.args[0].strip().lower() not in SUPPORTED_LANGS:
        await update.message.reply_text(t(lang, "language.invalid"))
        return
    new_lang = context.args[0].strip().lower()
    settings = db.get_settings(chat_id)
    settings.language = new_lang
    db.save_settings(settings)
    await update.message.reply_text(t(new_lang, "language.set", language=new_lang))


async def _set_float(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    attr: str,
    key: str,
) -> None:
    db = _db(context)
    chat_id = update.effective_chat.id
    lang = _lang(update, context)
    try:
        value = float(context.args[0])
        if value < 0:
            raise ValueError
    except (IndexError, ValueError):
        await _reply_error(update, context)
        return
    settings = db.get_settings(chat_id)
    setattr(settings, attr, value)
    db.save_settings(settings)
    await update.message.reply_text(t(lang, key, value=value))


@admin_only
async def cmd_setminbuy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # SPEC-v3: thresholds are configured in USDT
    await _set_float(update, context, "min_buy_usdt", "minbuy.set")


@admin_only
async def cmd_setwhale(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_float(update, context, "whale_usdt", "whale.set")


@admin_only
async def cmd_setup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Guided token-setup wizard (SPEC-v3 §5.1): ForceReply for the address.

    The actual flow (card -> track -> quick-config) lives in bot/callbacks.py;
    here we only arm the ``awaiting`` state and send the ForceReply prompt.
    """
    lang = _lang(update, context)
    user_id = update.effective_user.id if update.effective_user else 0
    try:
        from bot import callbacks as ui_callbacks

        await ui_callbacks.prompt_wizard_token(update.message, context, user_id, lang)
    except ImportError:  # pragma: no cover - callbacks module is shipped together
        logger.warning("bot.callbacks unavailable; /setup wizard disabled")
        await update.message.reply_text(t(lang, "error.generic"))


async def cmd_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = _db(context)
    lang = _lang(update, context)
    address = _resolve_token_arg(update, context)
    if address == "__invalid__":
        await update.message.reply_text(t(lang, "error.invalid_address"))
        return
    if address is None:
        await update.message.reply_text(t(lang, "token.list_empty"))
        return
    try:
        price_mon = await chain_price.get_price_mon(address)
        mcap_mon = await chain_price.get_mcap_mon(address)
        config = context.application.bot_data["config"]
        price_usd = price_mon * config.MON_USD_PRICE
        mcap_usd = mcap_mon * config.MON_USD_PRICE
        await update.message.reply_text(
            t(
                lang,
                "price.line",
                price_mon=f"{price_mon:.10f}".rstrip("0").rstrip(".") or "0",
                price_usd=f"{price_usd:,.2f}",
                mcap_mon=f"{mcap_mon:,.2f}",
                mcap_usd=f"{mcap_usd:,.2f}",
            )
        )
    except Exception:
        logger.exception("price command failed for %s", address)
        await _reply_error(update, context)


async def cmd_mcap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = _lang(update, context)
    address = _resolve_token_arg(update, context)
    if address == "__invalid__":
        await update.message.reply_text(t(lang, "error.invalid_address"))
        return
    if address is None:
        await update.message.reply_text(t(lang, "token.list_empty"))
        return
    try:
        mcap_mon = await chain_price.get_mcap_mon(address)
        config = context.application.bot_data["config"]
        mcap_usd = mcap_mon * config.MON_USD_PRICE
        await update.message.reply_text(
            t(lang, "buy.mcap", mcap_mon=f"{mcap_mon:,.2f}", mcap_usd=f"{mcap_usd:,.2f}")
        )
    except Exception:
        logger.exception("mcap command failed for %s", address)
        await _reply_error(update, context)


async def cmd_incubation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = _lang(update, context)
    address = _resolve_token_arg(update, context)
    if address == "__invalid__":
        await update.message.reply_text(t(lang, "error.invalid_address"))
        return
    if address is None:
        await update.message.reply_text(t(lang, "token.list_empty"))
        return
    try:
        info = await chain_incubation.get_curve_info(address)
    except Exception:
        logger.exception("incubation command failed for %s", address)
        await _reply_error(update, context)
        return

    lines = [t(lang, "incubation.title")]
    if info.graduated:
        lines.append(t(lang, "incubation.graduated"))
    elif info.is_incubating:
        pct = f"{info.progress_pct:.0f}" if info.progress_pct is not None else "?"
        raised = f"{info.mon_raised:,.2f}" if info.mon_raised is not None else "?"
        lines.append(t(lang, "incubation.progress", pct=pct, raised=raised))
    else:
        lines.append(t(lang, "incubation.not_incubating"))
    await update.message.reply_text("\n".join(lines))


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = _db(context)
    chat_id = update.effective_chat.id
    lang = _lang(update, context)
    stats = db.get_stats_24h(chat_id)
    lines = [
        t(lang, "stats.title"),
        t(
            lang,
            "stats.line",
            count=stats["count"],
            volume_mon=f"{stats['volume_mon']:,.2f}",
            volume_usd=f"{stats['volume_usd']:,.2f}",
        ),
    ]
    await update.message.reply_text("\n".join(lines))


async def cmd_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = _db(context)
    chat_id = update.effective_chat.id
    lang = _lang(update, context)
    top = db.get_top_buyers(chat_id)
    if not top:
        await update.message.reply_text(t(lang, "leaderboard.empty"))
        return
    lines = [t(lang, "leaderboard.title")]
    for rank, (buyer, total) in enumerate(top, start=1):
        short = f"{buyer[:6]}…{buyer[-4:]}" if len(buyer) > 13 else buyer
        lines.append(
            t(lang, "leaderboard.row", rank=rank, buyer=short, total=f"{total:,.2f}")
        )
    await update.message.reply_text("\n".join(lines))


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Open the button-based settings menu (anyone can view it)."""
    db = _db(context)
    chat_id = update.effective_chat.id
    lang = _lang(update, context)
    settings = db.get_settings(chat_id)
    await update.message.reply_text(
        t(lang, "ui.settings_title"),
        reply_markup=keyboards.build_settings_keyboard(settings, lang),
    )


HANDLERS = {
    "start": cmd_start,
    "help": cmd_help,
    "addtoken": cmd_addtoken,
    "removetoken": cmd_removetoken,
    "tokens": cmd_tokens,
    "setemoji": cmd_setemoji,
    "setwhaleemoji": cmd_setwhaleemoji,
    "setlanguage": cmd_setlanguage,
    "setup": cmd_setup,
    "setminbuy": cmd_setminbuy,
    "setwhale": cmd_setwhale,
    "price": cmd_price,
    "mcap": cmd_mcap,
    "incubation": cmd_incubation,
    "stats": cmd_stats,
    "leaderboard": cmd_leaderboard,
    "settings": cmd_settings,
    "about": cmd_about,
}
