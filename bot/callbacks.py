"""Button-based configuration menus (SPEC-v2 §8.2).

Registers a single ``CallbackQueryHandler`` with pattern ``^cfg:`` covering
the whole settings menu system, plus a ``MessageHandler`` that captures
guided (ForceReply) text input for custom emojis and custom amounts.

Callback data map:
    cfg:menu                              show/refresh the settings menu
    cfg:close                             delete the menu message
    cfg:back                              back to the settings menu
    cfg:help                              show the help text
    cfg:lang                              language picker
    cfg:lang:set:<code>                   set language (admin)
    cfg:emoji:<kind>                      emoji presets for kind buy|whale|sell
    cfg:emoji:<kind>:set:<emoji>          apply emoji preset (admin)
    cfg:emoji:<kind>:custom               guided custom emoji input (admin)
    cfg:amount:<field>                    amount presets for the field
    cfg:amount:<field>:set:<n>            apply amount preset (admin)
    cfg:amount:<field>:custom             guided custom amount input (admin)
    cfg:tokens                            tracked-token list with 🗑 buttons
    cfg:token:del:<address>               stop tracking a token (admin)
    cfg:toggle:<field>                    toggle sell_alerts|scanner_alerts (admin)
    cfg:setup                             guided token-setup wizard (admin)
    cfg:wizard:track                      confirm tracking the pasted token (admin)
    cfg:wizard:cancel                     abort the wizard
    cfg:wizard:done                       wizard quick-config done -> summary

Guided setup wizard (SPEC-v3 §5.1): /setup or the ➕ button arms
``chat_data["awaiting"] = "wizard_token"``; the user pastes a token address,
the bot shows an auto-detected card (🧪 incubation / 💱 DEX) with a
✅ Start tracking button, and tracking ends in a quick-config keyboard that
reuses the existing cfg:* sub-menus.

Anyone can open and browse the menus; only group admins can mutate settings
(same rule as the v1 commands; in private chats everyone is an admin).
"""

from __future__ import annotations

import logging
import re

from telegram import Chat, ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus
from telegram.ext import CallbackQueryHandler, ContextTypes, MessageHandler, filters

from core.i18n import SUPPORTED_LANGS, t

from bot import keyboards

logger = logging.getLogger(__name__)

EMOJI_ATTRS = set(keyboards.EMOJI_KIND_TO_ATTR.values())
AMOUNT_ATTRS = set(keyboards.AMOUNT_FIELDS)
TOGGLE_ATTRS = set(keyboards.TOGGLE_FIELDS)

EVM_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

# chat_data keys used by the setup wizard (SPEC-v3 §5.1)
AWAITING_WIZARD_TOKEN = "wizard_token"
WIZARD_PENDING_KEY = "wizard_pending"


def register_callbacks(app, deps) -> None:
    """Register the ``cfg:*`` callback handler and the guided-input handler.

    ``deps`` is the shared SimpleNamespace (db, listener, monitor, scanner,
    config) built in bot/main.py.
    """
    app.bot_data["deps"] = deps

    async def _callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await on_cfg_callback(update, context, deps)

    async def _text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await on_guided_text(update, context, deps)

    app.add_handler(CallbackQueryHandler(_callback, pattern=r"^cfg:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _text))


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _lang(deps, chat_id: int) -> str:
    return deps.db.get_settings(chat_id).language


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


async def _guard_admin(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str) -> bool:
    """Answer the callback with an admin-only alert if not admin."""
    if await _is_admin(update, context):
        return True
    await update.callback_query.answer(t(lang, "error.admin_only"), show_alert=True)
    return False


async def _edit(query, text: str, reply_markup=None) -> None:
    """Edit the menu message; fall back to a new message if editing fails."""
    try:
        await query.edit_message_text(text, reply_markup=reply_markup)
    except Exception:
        logger.debug("edit_message_text failed; sending a new message", exc_info=True)
        await query.message.reply_text(text, reply_markup=reply_markup)


async def _show_settings_menu(query, deps, chat_id: int, lang: str) -> None:
    settings = deps.db.get_settings(chat_id)
    await _edit(
        query,
        t(lang, "ui.settings_title"),
        reply_markup=keyboards.build_settings_keyboard(settings, lang),
    )


async def _prompt_custom(query, context, lang: str, field: str, prompt_key: str) -> None:
    """Ask for a custom value via ForceReply and arm the capture handler."""
    context.chat_data["awaiting"] = field
    context.chat_data["awaiting_user"] = query.from_user.id
    await query.message.reply_text(
        t(lang, prompt_key),
        reply_markup=ForceReply(selective=True),
    )


# ---------------------------------------------------------------------------
# guided token-setup wizard (SPEC-v3 §5.1)
# ---------------------------------------------------------------------------


async def prompt_wizard_token(message, context, user_id: int, lang: str) -> None:
    """Step 1 of the wizard: ForceReply asking for the token address.

    Shared by the /setup command (bot/handlers.py) and the cfg:setup button.
    """
    context.chat_data["awaiting"] = AWAITING_WIZARD_TOKEN
    context.chat_data["awaiting_user"] = user_id
    context.chat_data.pop(WIZARD_PENDING_KEY, None)
    await message.reply_text(
        t(lang, "wizard.prompt_token"),
        reply_markup=ForceReply(selective=True),
    )


def _fmt_price(price_mon: float) -> str:
    if not price_mon or price_mon <= 0:
        return "?"
    return f"{price_mon:.10f}".rstrip("0").rstrip(".") or "0"


def _kind_label(kind: str, lang: str) -> str:
    if kind == "curve":
        return t(lang, "wizard.kind_curve")
    return t(lang, "wizard.kind_dex")


async def _wizard_show_card(message, context, lang: str, info, price_mon: float) -> None:
    """Step 2: token card with auto-detected kind + track/cancel buttons."""
    context.chat_data[WIZARD_PENDING_KEY] = {
        "address": info.address,
        "name": info.name or info.address,
        "symbol": info.symbol or "?",
        "kind": info.kind or "unknown",
    }
    await message.reply_text(
        t(
            lang,
            "wizard.card",
            name=info.name or info.address,
            symbol=info.symbol or "?",
            kind=_kind_label(info.kind, lang),
            price=_fmt_price(price_mon),
        ),
        reply_markup=keyboards.build_wizard_card_keyboard(lang),
    )


async def _wizard_track(query, context, deps, chat_id: int, lang: str) -> None:
    """Step 3: add the pending token and show the quick-config keyboard."""
    pending = context.chat_data.pop(WIZARD_PENDING_KEY, None)
    if not pending:
        await query.answer()
        return
    address = pending["address"]
    deps.db.add_token(chat_id, address, pending.get("kind") or "unknown")
    listener = getattr(deps, "listener", None)
    if listener is not None:
        try:
            await listener.add_token(address)
        except Exception:
            logger.exception("listener.add_token failed for %s", address)
    await query.answer()
    await _edit(
        query,
        t(lang, "wizard.tracked", name=pending["name"], symbol=pending["symbol"]),
        reply_markup=keyboards.build_wizard_quick_keyboard(lang),
    )


async def _wizard_done(query, deps, chat_id: int, lang: str) -> None:
    """Final step: summary of the current (USDT) settings."""
    settings = deps.db.get_settings(chat_id)
    await query.answer()
    await _edit(
        query,
        t(
            lang,
            "settings.show",
            language=settings.language,
            buy_emoji=settings.buy_emoji,
            whale_emoji=settings.whale_emoji,
            min_buy_usdt=f"{settings.min_buy_usdt:g}",
            whale_usdt=f"{settings.whale_usdt:g}",
            emoji_step_usdt=f"{settings.emoji_step_usdt:g}",
        ),
        reply_markup=_back_markup(lang),
    )


# ---------------------------------------------------------------------------
# callback dispatch
# ---------------------------------------------------------------------------


async def on_cfg_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, deps) -> None:
    """Handle every ``cfg:*`` callback (registered with pattern ``^cfg:``)."""
    query = update.callback_query
    if query is None or not query.data:
        return
    data = query.data
    parts = data.split(":")
    chat_id = update.effective_chat.id
    lang = _lang(deps, chat_id)

    action = parts[1] if len(parts) > 1 else ""

    # --- navigation (anyone) ------------------------------------------------
    if action == "menu" or action == "back":
        await query.answer()
        await _show_settings_menu(query, deps, chat_id, lang)
        return

    if action == "close":
        await query.answer()
        try:
            await query.message.delete()
        except Exception:
            logger.debug("could not delete menu message", exc_info=True)
        return

    if action == "help":
        await query.answer()
        back = InlineKeyboardMarkup(
            [[InlineKeyboardButton(t(lang, "ui.btn_back"), callback_data="cfg:back")]]
        )
        await _edit(query, t(lang, "help"), reply_markup=back)
        return

    if action == "lang" and len(parts) == 2:
        await query.answer()
        current = deps.db.get_settings(chat_id).language
        await _edit(
            query,
            t(lang, "ui.choose_language"),
            reply_markup=keyboards.build_language_keyboard(current),
        )
        return

    if action == "emoji" and len(parts) == 3:
        kind = parts[2]
        if kind not in keyboards.EMOJI_KIND_TO_ATTR:
            await query.answer()
            return
        await query.answer()
        await _edit(
            query,
            t(lang, "ui.choose_emoji"),
            reply_markup=keyboards.build_emoji_preset_keyboard(kind),
        )
        return

    if action == "amount" and len(parts) == 3:
        field = parts[2]
        if field not in AMOUNT_ATTRS:
            await query.answer()
            return
        await query.answer()
        await _edit(
            query,
            t(lang, "ui.choose_amount"),
            reply_markup=keyboards.build_amount_keyboard(field),
        )
        return

    if action == "tokens":
        await query.answer()
        await _show_tokens(query, deps, chat_id, lang)
        return

    if action == "wizard" and len(parts) == 3 and parts[2] == "cancel":
        context.chat_data.pop("awaiting", None)
        context.chat_data.pop("awaiting_user", None)
        context.chat_data.pop(WIZARD_PENDING_KEY, None)
        await query.answer()
        try:
            await query.message.delete()
        except Exception:
            logger.debug("could not delete wizard message", exc_info=True)
        return

    if action == "wizard" and len(parts) == 3 and parts[2] == "done":
        await _wizard_done(query, deps, chat_id, lang)
        return

    # --- mutations (admins only) --------------------------------------------
    if action == "setup":
        if not await _guard_admin(update, context, lang):
            return
        await query.answer()
        await prompt_wizard_token(query.message, context, query.from_user.id, lang)
        return

    if action == "wizard" and len(parts) == 3 and parts[2] == "track":
        if not await _guard_admin(update, context, lang):
            return
        await _wizard_track(query, context, deps, chat_id, lang)
        return

    if action == "lang" and len(parts) == 4 and parts[2] == "set":
        code = parts[3]
        if code not in SUPPORTED_LANGS:
            await query.answer(t(lang, "language.invalid", languages="en, es, zh"))
            return
        if not await _guard_admin(update, context, lang):
            return
        settings = deps.db.get_settings(chat_id)
        settings.language = code
        deps.db.save_settings(settings)
        await query.answer(t(code, "ui.saved"))
        await _show_settings_menu(query, deps, chat_id, code)
        return

    if action == "emoji" and len(parts) >= 4:
        kind = parts[2]
        attr = keyboards.EMOJI_KIND_TO_ATTR.get(kind)
        if attr is None:
            await query.answer()
            return
        if parts[3] == "set" and len(parts) == 5:
            if not await _guard_admin(update, context, lang):
                return
            emoji = parts[4]
            if not _valid_emoji(emoji):
                await query.answer(t(lang, "ui.invalid_emoji"), show_alert=True)
                return
            settings = deps.db.get_settings(chat_id)
            setattr(settings, attr, emoji)
            deps.db.save_settings(settings)
            await query.answer(t(lang, "ui.saved"))
            await _show_settings_menu(query, deps, chat_id, lang)
            return
        if parts[3] == "custom":
            if not await _guard_admin(update, context, lang):
                return
            await query.answer()
            await _prompt_custom(query, context, lang, attr, "ui.custom_prompt_emoji")
            return

    if action == "amount" and len(parts) >= 4:
        field = parts[2]
        if field not in AMOUNT_ATTRS:
            await query.answer()
            return
        if parts[3] == "set" and len(parts) == 5:
            if not await _guard_admin(update, context, lang):
                return
            try:
                value = float(parts[4])
                if value <= 0:
                    raise ValueError
            except ValueError:
                await query.answer(t(lang, "ui.invalid_number"), show_alert=True)
                return
            settings = deps.db.get_settings(chat_id)
            setattr(settings, field, value)
            deps.db.save_settings(settings)
            await query.answer(t(lang, "ui.saved"))
            await _show_settings_menu(query, deps, chat_id, lang)
            return
        if parts[3] == "custom":
            if not await _guard_admin(update, context, lang):
                return
            await query.answer()
            await _prompt_custom(query, context, lang, field, "ui.custom_prompt_amount")
            return

    if action == "token" and len(parts) == 4 and parts[2] == "del":
        if not await _guard_admin(update, context, lang):
            return
        address = parts[3]
        removed = deps.db.remove_token(chat_id, address)
        listener = getattr(deps, "listener", None)
        if removed and listener is not None:
            try:
                await listener.remove_token(address)
            except Exception:
                logger.exception("listener.remove_token failed for %s", address)
        await query.answer(t(lang, "ui.token_deleted") if removed else t(lang, "token.not_found", address=address))
        await _show_tokens(query, deps, chat_id, lang)
        return

    if action == "toggle" and len(parts) == 3:
        field = parts[2]
        if field not in TOGGLE_ATTRS:
            await query.answer()
            return
        if not await _guard_admin(update, context, lang):
            return
        settings = deps.db.get_settings(chat_id)
        current = bool(getattr(settings, field, False))
        setattr(settings, field, not current)
        deps.db.save_settings(settings)
        await query.answer(t(lang, "ui.saved"))
        await _show_settings_menu(query, deps, chat_id, lang)
        return

    # unknown cfg:* callback — just acknowledge
    await query.answer()


async def _show_tokens(query, deps, chat_id: int, lang: str) -> None:
    tokens = deps.db.list_tokens(chat_id)
    if not tokens:
        await _edit(query, t(lang, "ui.no_tokens"), reply_markup=_back_markup(lang))
        return
    await _edit(
        query,
        t(lang, "token.list_header", count=len(tokens)),
        reply_markup=keyboards.build_tokens_keyboard(tokens, lang),
    )


def _back_markup(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(t(lang, "ui.btn_back"), callback_data="cfg:back")]]
    )


# ---------------------------------------------------------------------------
# guided text input (ForceReply capture)
# ---------------------------------------------------------------------------


def _valid_emoji(text: str) -> bool:
    """1-4 characters, none of them alphanumeric."""
    text = text.strip()
    if not 1 <= len(text) <= 4:
        return False
    return not any(ch.isalnum() for ch in text)


async def on_guided_text(update: Update, context: ContextTypes.DEFAULT_TYPE, deps) -> None:
    """Capture a ForceReply answer for ``context.chat_data['awaiting']``."""
    if update.message is None or update.effective_chat is None:
        return
    awaiting = context.chat_data.get("awaiting") if context.chat_data is not None else None
    if not awaiting:
        return
    # only the admin who started the flow may answer it
    expected_user = context.chat_data.get("awaiting_user")
    if expected_user is not None and update.effective_user is not None:
        if update.effective_user.id != expected_user:
            return

    chat_id = update.effective_chat.id
    lang = _lang(deps, chat_id)
    text = (update.message.text or "").strip()

    # --- setup wizard: a token address was requested ------------------------
    if awaiting == AWAITING_WIZARD_TOKEN:
        if not await _is_admin(update, context):
            await update.message.reply_text(t(lang, "error.admin_only"))
            return
        if not EVM_ADDRESS_RE.match(text):
            await update.message.reply_text(t(lang, "wizard.invalid_token"))
            return  # keep waiting for a valid address
        try:
            from chain import price as chain_price

            info = await chain_price.get_token_info(text)
            try:
                price_mon = await chain_price.get_price_mon(info.address)
            except Exception:  # noqa: BLE001 - price is best effort
                price_mon = 0.0
        except Exception:
            logger.exception("wizard token fetch failed for %s", text)
            await update.message.reply_text(t(lang, "wizard.fetch_failed"))
            return  # keep waiting so the admin can retry
        context.chat_data.pop("awaiting", None)
        context.chat_data.pop("awaiting_user", None)
        await _wizard_show_card(update.message, context, lang, info, price_mon)
        return

    if awaiting in EMOJI_ATTRS:
        if not _valid_emoji(text):
            await update.message.reply_text(t(lang, "ui.invalid_emoji"))
            return
        value = text
    elif awaiting in AMOUNT_ATTRS:
        try:
            value = float(text)
            if value <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text(t(lang, "ui.invalid_number"))
            return
    else:
        # unknown awaiting state — clear and ignore
        context.chat_data.pop("awaiting", None)
        context.chat_data.pop("awaiting_user", None)
        return

    if not await _is_admin(update, context):
        await update.message.reply_text(t(lang, "error.admin_only"))
        return

    settings = deps.db.get_settings(chat_id)
    setattr(settings, awaiting, value)
    deps.db.save_settings(settings)
    context.chat_data.pop("awaiting", None)
    context.chat_data.pop("awaiting_user", None)
    await update.message.reply_text(
        t(lang, "ui.saved"),
        reply_markup=_back_markup(lang),
    )
