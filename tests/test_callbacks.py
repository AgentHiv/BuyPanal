"""Tests for bot.callbacks (cfg:* menus) and bot.keyboards (SPEC-v2 §8).

Telegram objects are replaced by duck-typed fakes; the db is an in-memory
fake. No live RPC or Telegram calls.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram import ForceReply, InlineKeyboardMarkup
from telegram.constants import ChatMemberStatus

from core.models import GroupSettings

from bot import callbacks, keyboards


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------


class FakeDB:
    def __init__(self):
        self._settings = {}
        self._tokens = {}
        self.saved = []

    def get_settings(self, chat_id):
        if chat_id not in self._settings:
            self._settings[chat_id] = GroupSettings(chat_id=chat_id)
        return self._settings[chat_id]

    def save_settings(self, settings):
        self._settings[settings.chat_id] = settings
        self.saved.append(settings)

    def list_tokens(self, chat_id):
        return list(self._tokens.get(chat_id, []))

    def add_token(self, chat_id, address, kind="unknown"):
        toks = self._tokens.setdefault(chat_id, [])
        if address in toks:
            return False
        toks.append(address)
        return True

    def remove_token(self, chat_id, address):
        toks = self._tokens.get(chat_id, [])
        if address in toks:
            toks.remove(address)
            return True
        return False

    def all_tracked_tokens(self):
        out = {}
        for chat_id, toks in self._tokens.items():
            for addr in toks:
                out.setdefault(addr, []).append(chat_id)
        return out


@pytest.fixture
def db():
    return FakeDB()


@pytest.fixture
def deps(db):
    return SimpleNamespace(
        db=db, listener=AsyncMock(), monitor=None, scanner=None, config=None
    )


def make_context(admin_status=ChatMemberStatus.ADMINISTRATOR):
    bot = SimpleNamespace(
        get_chat_member=AsyncMock(return_value=SimpleNamespace(status=admin_status))
    )
    return SimpleNamespace(chat_data={}, bot_data={}, bot=bot, args=None)


def make_callback_update(data, chat_type="group", chat_id=100, user_id=1):
    query = SimpleNamespace(
        data=data,
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
        from_user=SimpleNamespace(id=user_id),
        message=SimpleNamespace(reply_text=AsyncMock(), delete=AsyncMock()),
    )
    return SimpleNamespace(
        callback_query=query,
        effective_chat=SimpleNamespace(id=chat_id, type=chat_type),
        effective_user=SimpleNamespace(id=user_id),
        message=None,
    )


def make_text_update(text, chat_type="group", chat_id=100, user_id=1):
    return SimpleNamespace(
        callback_query=None,
        effective_chat=SimpleNamespace(id=chat_id, type=chat_type),
        effective_user=SimpleNamespace(id=user_id),
        message=SimpleNamespace(text=text, reply_text=AsyncMock()),
    )


def all_buttons(markup: InlineKeyboardMarkup):
    return [btn for row in markup.inline_keyboard for btn in row]


def callback_datas(markup: InlineKeyboardMarkup):
    return [btn.callback_data for btn in all_buttons(markup) if btn.callback_data]


ADDR1 = "0x" + "aa" * 20
ADDR2 = "0x" + "bb" * 20


# ---------------------------------------------------------------------------
# keyboards
# ---------------------------------------------------------------------------


def test_settings_keyboard_has_all_menu_entries():
    settings = GroupSettings(chat_id=100)
    markup = keyboards.build_settings_keyboard(settings, "en")
    data = callback_datas(markup)
    for expected in (
        "cfg:lang",
        "cfg:emoji:buy",
        "cfg:emoji:whale",
        "cfg:emoji:sell",
        "cfg:amount:min_buy_mon",
        "cfg:amount:whale_mon",
        "cfg:amount:emoji_step_mon",
        "cfg:tokens",
        "cfg:toggle:sell_alerts",
        "cfg:toggle:scanner_alerts",
        "cfg:close",
    ):
        assert expected in data, expected
    # all buttons are translated (no raw "ui." keys leak through)
    for btn in all_buttons(markup):
        assert not btn.text.startswith("ui.")


def test_language_keyboard_marks_current():
    markup = keyboards.build_language_keyboard("es")
    labels = {btn.callback_data: btn.text for btn in all_buttons(markup)}
    assert labels["cfg:lang:set:es"].startswith("✅")
    assert not labels["cfg:lang:set:en"].startswith("✅")
    assert "cfg:back" in labels


def test_emoji_preset_keyboard_has_10_plus_presets_and_custom():
    for kind in ("buy", "whale", "sell"):
        markup = keyboards.build_emoji_preset_keyboard(kind)
        data = callback_datas(markup)
        presets = [d for d in data if d.startswith(f"cfg:emoji:{kind}:set:")]
        assert len(presets) >= 10, kind
        assert f"cfg:emoji:{kind}:custom" in data
        assert "cfg:back" in data
    # the spec presets are included
    buy_data = callback_datas(keyboards.build_emoji_preset_keyboard("buy"))
    assert "cfg:emoji:buy:set:🟢" in buy_data
    whale_data = callback_datas(keyboards.build_emoji_preset_keyboard("whale"))
    assert "cfg:emoji:whale:set:🐋" in whale_data


def test_amount_keyboard_presets_and_custom():
    markup = keyboards.build_amount_keyboard("min_buy_mon")
    data = callback_datas(markup)
    for n in ("1", "5", "10", "50", "100"):
        assert f"cfg:amount:min_buy_mon:set:{n}" in data
    assert "cfg:amount:min_buy_mon:custom" in data
    assert "cfg:back" in data


def test_tokens_keyboard_has_delete_button_per_token():
    markup = keyboards.build_tokens_keyboard([ADDR1, ADDR2], "en")
    data = callback_datas(markup)
    assert f"cfg:token:del:{ADDR1}" in data
    assert f"cfg:token:del:{ADDR2}" in data
    assert "🗑" in all_buttons(markup)[0].text
    assert "cfg:back" in data


def test_start_keyboard_buttons():
    keyboards.set_bot_username("MonadBuyBot")
    try:
        markup = keyboards.build_start_keyboard("en")
    finally:
        keyboards.set_bot_username(None)
    data = callback_datas(markup)
    assert "cfg:menu" in data
    assert "cfg:help" in data
    urls = [btn.url for btn in all_buttons(markup) if btn.url]
    assert urls == ["https://t.me/MonadBuyBot?startgroup=true"]


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------


def test_register_callbacks_adds_handlers_and_deps(deps):
    added = []

    class FakeApp:
        def __init__(self):
            self.bot_data = {}

        def add_handler(self, handler):
            added.append(handler)

    app = FakeApp()
    callbacks.register_callbacks(app, deps)
    assert app.bot_data["deps"] is deps
    assert len(added) == 2  # CallbackQueryHandler + MessageHandler
    from telegram.ext import CallbackQueryHandler, MessageHandler

    assert isinstance(added[0], CallbackQueryHandler)
    assert isinstance(added[1], MessageHandler)
    assert added[0].pattern.pattern == r"^cfg:"


# ---------------------------------------------------------------------------
# navigation (anyone)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_menu_opens_settings_keyboard(deps):
    update = make_callback_update("cfg:menu")
    context = make_context()
    await callbacks.on_cfg_callback(update, context, deps)
    update.callback_query.answer.assert_awaited_once()
    args = update.callback_query.edit_message_text.await_args
    assert args.kwargs["reply_markup"] is not None
    assert "cfg:toggle:sell_alerts" in callback_datas(args.kwargs["reply_markup"])


@pytest.mark.asyncio
async def test_close_deletes_message(deps):
    update = make_callback_update("cfg:close")
    await callbacks.on_cfg_callback(update, make_context(), deps)
    update.callback_query.message.delete.assert_awaited_once()


# ---------------------------------------------------------------------------
# mutations as admin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_language_set_changes_settings(deps, db):
    update = make_callback_update("cfg:lang:set:es")
    await callbacks.on_cfg_callback(update, make_context(), deps)
    assert db.get_settings(100).language == "es"
    assert db.saved, "settings should be persisted"
    # menu refreshed in the new language
    text = update.callback_query.edit_message_text.await_args.args[0]
    assert "ui.settings_title" not in text


@pytest.mark.asyncio
async def test_emoji_preset_set(deps, db):
    update = make_callback_update("cfg:emoji:buy:set:🔥")
    await callbacks.on_cfg_callback(update, make_context(), deps)
    assert db.get_settings(100).buy_emoji == "🔥"


@pytest.mark.asyncio
async def test_amount_preset_set(deps, db):
    update = make_callback_update("cfg:amount:whale_mon:set:50")
    await callbacks.on_cfg_callback(update, make_context(), deps)
    assert db.get_settings(100).whale_mon == 50.0


@pytest.mark.asyncio
async def test_amount_preset_rejects_non_positive(deps, db):
    update = make_callback_update("cfg:amount:min_buy_mon:set:0")
    await callbacks.on_cfg_callback(update, make_context(), deps)
    assert db.get_settings(100).min_buy_mon == 1.0  # unchanged default
    update.callback_query.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_toggle_sell_alerts(deps, db):
    update = make_callback_update("cfg:toggle:sell_alerts")
    await callbacks.on_cfg_callback(update, make_context(), deps)
    assert db.get_settings(100).sell_alerts is True
    # toggle back off
    update2 = make_callback_update("cfg:toggle:sell_alerts")
    await callbacks.on_cfg_callback(update2, make_context(), deps)
    assert db.get_settings(100).sell_alerts is False


@pytest.mark.asyncio
async def test_toggle_scanner_alerts(deps, db):
    update = make_callback_update("cfg:toggle:scanner_alerts")
    await callbacks.on_cfg_callback(update, make_context(), deps)
    assert db.get_settings(100).scanner_alerts is True


@pytest.mark.asyncio
async def test_token_delete_removes_from_db_and_listener(deps, db):
    db.add_token(100, ADDR1)
    update = make_callback_update(f"cfg:token:del:{ADDR1}")
    await callbacks.on_cfg_callback(update, make_context(), deps)
    assert db.list_tokens(100) == []
    deps.listener.remove_token.assert_awaited_once_with(ADDR1)


# ---------------------------------------------------------------------------
# admin enforcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_admin_cannot_mutate(deps, db):
    context = make_context(admin_status=ChatMemberStatus.MEMBER)
    update = make_callback_update("cfg:toggle:sell_alerts")
    await callbacks.on_cfg_callback(update, context, deps)
    assert not db.saved
    assert getattr(db.get_settings(100), "sell_alerts", False) is False
    # alerted via callback answer
    kwargs = update.callback_query.answer.await_args.kwargs
    assert kwargs.get("show_alert") is True


@pytest.mark.asyncio
async def test_non_admin_can_browse_menus(deps):
    context = make_context(admin_status=ChatMemberStatus.MEMBER)
    update = make_callback_update("cfg:emoji:buy")
    await callbacks.on_cfg_callback(update, context, deps)
    update.callback_query.edit_message_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_private_chat_allows_mutation_without_member_check(deps, db):
    context = make_context(admin_status=ChatMemberStatus.MEMBER)
    update = make_callback_update("cfg:lang:set:zh", chat_type="private")
    await callbacks.on_cfg_callback(update, context, deps)
    context.bot.get_chat_member.assert_not_awaited()
    assert db.get_settings(100).language == "zh"


# ---------------------------------------------------------------------------
# guided input (ForceReply capture)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_custom_emoji_flow(deps, db):
    context = make_context()
    # 1) press "custom" -> ForceReply prompt + awaiting state
    update = make_callback_update("cfg:emoji:whale:custom")
    await callbacks.on_cfg_callback(update, context, deps)
    assert context.chat_data["awaiting"] == "whale_emoji"
    prompt = update.callback_query.message.reply_text.await_args
    assert isinstance(prompt.kwargs["reply_markup"], ForceReply)

    # 2) user replies with an emoji -> saved, state cleared
    text_update = make_text_update("🦈")
    await callbacks.on_guided_text(text_update, context, deps)
    assert db.get_settings(100).whale_emoji == "🦈"
    assert "awaiting" not in context.chat_data
    confirm = text_update.message.reply_text.await_args
    back_data = callback_datas(confirm.kwargs["reply_markup"])
    assert "cfg:back" in back_data


@pytest.mark.asyncio
async def test_guided_input_rejects_invalid_emoji(deps, db):
    context = make_context()
    await callbacks.on_cfg_callback(
        make_callback_update("cfg:emoji:buy:custom"), context, deps
    )
    text_update = make_text_update("abc")  # alphanumeric -> invalid
    await callbacks.on_guided_text(text_update, context, deps)
    assert db.get_settings(100).buy_emoji == "🟢"  # unchanged
    assert context.chat_data["awaiting"] == "buy_emoji"  # still waiting


@pytest.mark.asyncio
async def test_guided_input_amount_validation(deps, db):
    context = make_context()
    await callbacks.on_cfg_callback(
        make_callback_update("cfg:amount:emoji_step_mon:custom"), context, deps
    )
    assert context.chat_data["awaiting"] == "emoji_step_mon"

    bad = make_text_update("-3")
    await callbacks.on_guided_text(bad, context, deps)
    assert db.get_settings(100).emoji_step_mon == 10.0  # unchanged
    assert "awaiting" in context.chat_data

    good = make_text_update("2.5")
    await callbacks.on_guided_text(good, context, deps)
    assert db.get_settings(100).emoji_step_mon == 2.5
    assert "awaiting" not in context.chat_data


@pytest.mark.asyncio
async def test_guided_input_ignores_other_users(deps, db):
    context = make_context()
    await callbacks.on_cfg_callback(
        make_callback_update("cfg:emoji:buy:custom", user_id=1), context, deps
    )
    intruder = make_text_update("🔥", user_id=999)
    await callbacks.on_guided_text(intruder, context, deps)
    assert db.get_settings(100).buy_emoji == "🟢"  # unchanged
    assert context.chat_data["awaiting"] == "buy_emoji"
    intruder.message.reply_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_guided_input_noop_without_awaiting(deps):
    context = make_context()
    update = make_text_update("hello")
    await callbacks.on_guided_text(update, context, deps)
    update.message.reply_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_guided_input_non_admin_rejected(deps, db):
    context = make_context()
    await callbacks.on_cfg_callback(
        make_callback_update("cfg:amount:min_buy_mon:custom"), context, deps
    )
    # user loses admin rights before answering
    context.bot.get_chat_member = AsyncMock(
        return_value=SimpleNamespace(status=ChatMemberStatus.MEMBER)
    )
    update = make_text_update("5")
    await callbacks.on_guided_text(update, context, deps)
    assert db.get_settings(100).min_buy_mon == 1.0  # unchanged
