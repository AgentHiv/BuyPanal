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

Anyone can open and browse the menus; only group admins can mutate settings
(same rule as the v1 commands; in private chats everyone is an admin).
"""

from __future__ import annotations

import logging

from telegram import Chat, ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus
from telegram.ext import CallbackQueryHandler, ContextTypes, MessageHandler, filters

from core.i18n import SUPPORTED_LANGS, t

from bot import keyboards

logger = logging.getLogger(__name__)

EMOJI_ATTRS = set(keyboards.EMOJI_KIND_TO_ATTR.values())
AMOUNT_ATTRS = set(keyboards.AMOUNT_FIELDS)
TOGGLE_ATTRS = set(keyboards.TOGGLE_FIELDS)


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

    # --- mutations (admins only) --------------------------------------------
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
