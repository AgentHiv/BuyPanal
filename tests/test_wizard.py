"""Tests for the guided token-setup wizard (SPEC-v3 §5.1).

Covers the full flow — prompt -> invalid address -> fetch failure -> card
-> track -> done — plus the admin check, using duck-typed Telegram fakes
and a mocked chain (no live RPC / Telegram).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram import ForceReply
from telegram.constants import ChatMemberStatus

from core.models import GroupSettings, TokenInfo

from bot import callbacks, keyboards

ADDR = "0x" + "aa" * 20


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------


class FakeDB:
    def __init__(self):
        self._settings = {}
        self._tokens = {}

    def get_settings(self, chat_id):
        if chat_id not in self._settings:
            self._settings[chat_id] = GroupSettings(chat_id=chat_id)
        return self._settings[chat_id]

    def save_settings(self, settings):
        self._settings[settings.chat_id] = settings

    def add_token(self, chat_id, address, kind="unknown"):
        toks = self._tokens.setdefault(chat_id, [])
        if address in toks:
            return False
        toks.append(address)
        return True

    def list_tokens(self, chat_id):
        return list(self._tokens.get(chat_id, []))


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


def all_buttons(markup):
    return [btn for row in markup.inline_keyboard for btn in row]


def callback_datas(markup):
    return [btn.callback_data for btn in all_buttons(markup) if btn.callback_data]


def mock_token(monkeypatch, info=None, price=0.0):
    """Mock chain.price.get_token_info / get_price_mon used by the wizard."""
    if info is None:
        info = TokenInfo(
            address=ADDR, name="Test Token", symbol="TST", decimals=18,
            total_supply=1_000_000.0, kind="curve",
        )

    async def fake_info(address):
        return info

    async def fake_price(address):
        return price

    monkeypatch.setattr("chain.price.get_token_info", fake_info)
    monkeypatch.setattr("chain.price.get_price_mon", fake_price)
    return info


# ---------------------------------------------------------------------------
# keyboards
# ---------------------------------------------------------------------------


def test_start_keyboard_has_setup_button():
    keyboards.set_bot_username(None)
    markup = keyboards.build_start_keyboard("en")
    assert "cfg:setup" in callback_datas(markup)
    labels = [btn.text for btn in all_buttons(markup)]
    assert any("➕" in label for label in labels)


def test_wizard_card_keyboard():
    markup = keyboards.build_wizard_card_keyboard("en")
    data = callback_datas(markup)
    assert "cfg:wizard:track" in data
    assert "cfg:wizard:cancel" in data


def test_wizard_quick_keyboard_reuses_cfg_menus():
    markup = keyboards.build_wizard_quick_keyboard("en")
    data = callback_datas(markup)
    for expected in (
        "cfg:emoji:buy",
        "cfg:amount:min_buy_usdt",
        "cfg:amount:whale_usdt",
        "cfg:lang",
        "cfg:wizard:done",
    ):
        assert expected in data, expected
    for btn in all_buttons(markup):
        assert not btn.text.startswith(("ui.", "wizard."))


def test_setup_command_registered():
    from bot.handlers import COMMANDS, HANDLERS

    assert "setup" in HANDLERS
    assert any(name == "setup" for name, _desc, _admin in COMMANDS)


# ---------------------------------------------------------------------------
# flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_setup_button_prompts_with_forcereply(deps):
    context = make_context()
    update = make_callback_update("cfg:setup")
    await callbacks.on_cfg_callback(update, context, deps)
    assert context.chat_data["awaiting"] == "wizard_token"
    prompt = update.callback_query.message.reply_text.await_args
    assert isinstance(prompt.kwargs["reply_markup"], ForceReply)
    text = prompt.args[0]
    assert "0x" in text and "wizard.prompt_token" not in text


@pytest.mark.asyncio
async def test_setup_requires_admin(deps, db):
    context = make_context(admin_status=ChatMemberStatus.MEMBER)
    update = make_callback_update("cfg:setup")
    await callbacks.on_cfg_callback(update, context, deps)
    assert "awaiting" not in context.chat_data
    update.callback_query.message.reply_text.assert_not_awaited()
    kwargs = update.callback_query.answer.await_args.kwargs
    assert kwargs.get("show_alert") is True


@pytest.mark.asyncio
async def test_invalid_address_keeps_waiting(deps):
    context = make_context()
    await callbacks.on_cfg_callback(make_callback_update("cfg:setup"), context, deps)
    update = make_text_update("not-an-address")
    await callbacks.on_guided_text(update, context, deps)
    assert context.chat_data["awaiting"] == "wizard_token"  # still waiting
    reply = update.message.reply_text.await_args.args[0]
    assert "wizard.invalid_token" not in reply  # translated


@pytest.mark.asyncio
async def test_fetch_failure_keeps_waiting(deps, monkeypatch):
    context = make_context()
    await callbacks.on_cfg_callback(make_callback_update("cfg:setup"), context, deps)

    async def broken(address):
        raise ConnectionError("rpc down")

    monkeypatch.setattr("chain.price.get_token_info", broken)
    update = make_text_update(ADDR)
    await callbacks.on_guided_text(update, context, deps)
    assert context.chat_data["awaiting"] == "wizard_token"
    reply = update.message.reply_text.await_args.args[0]
    assert "wizard.fetch_failed" not in reply


@pytest.mark.asyncio
async def test_full_wizard_flow(deps, db, monkeypatch):
    context = make_context()
    mock_token(monkeypatch, price=0.00001234)

    # 1) /setup (or ➕ button) -> ForceReply prompt
    await callbacks.on_cfg_callback(make_callback_update("cfg:setup"), context, deps)
    assert context.chat_data["awaiting"] == "wizard_token"

    # 2) paste the address -> card with kind + track/cancel buttons
    update = make_text_update(ADDR)
    await callbacks.on_guided_text(update, context, deps)
    assert "awaiting" not in context.chat_data
    assert context.chat_data["wizard_pending"]["address"] == ADDR
    card = update.message.reply_text.await_args
    card_text = card.args[0]
    assert "Test Token" in card_text and "TST" in card_text
    assert "🧪" in card_text  # auto-detected incubation kind
    card_data = callback_datas(card.kwargs["reply_markup"])
    assert "cfg:wizard:track" in card_data and "cfg:wizard:cancel" in card_data

    # 3) ✅ Start tracking -> db + listener + quick-config keyboard
    track_update = make_callback_update("cfg:wizard:track")
    await callbacks.on_cfg_callback(track_update, context, deps)
    assert db.list_tokens(100) == [ADDR]
    deps.listener.add_token.assert_awaited_once_with(ADDR)
    assert "wizard_pending" not in context.chat_data
    edited = track_update.callback_query.edit_message_text.await_args
    quick_data = callback_datas(edited.kwargs["reply_markup"])
    for expected in ("cfg:emoji:buy", "cfg:amount:min_buy_usdt", "cfg:lang", "cfg:wizard:done"):
        assert expected in quick_data, expected

    # 4) ✔️ Done -> settings summary with USDT values
    done_update = make_callback_update("cfg:wizard:done")
    await callbacks.on_cfg_callback(done_update, context, deps)
    summary = done_update.callback_query.edit_message_text.await_args.args[0]
    assert "USDT" in summary and "settings.show" not in summary
    assert "5" in summary  # default min buy 5 USDT


@pytest.mark.asyncio
async def test_dex_kind_card(deps, monkeypatch):
    context = make_context()
    info = TokenInfo(
        address=ADDR, name="Dex Token", symbol="DEX", decimals=18,
        total_supply=1.0, kind="dex",
    )
    mock_token(monkeypatch, info=info)
    await callbacks.on_cfg_callback(make_callback_update("cfg:setup"), context, deps)
    update = make_text_update(ADDR)
    await callbacks.on_guided_text(update, context, deps)
    card_text = update.message.reply_text.await_args.args[0]
    assert "💱" in card_text


@pytest.mark.asyncio
async def test_cancel_clears_state_and_deletes(deps, monkeypatch):
    context = make_context()
    mock_token(monkeypatch)
    await callbacks.on_cfg_callback(make_callback_update("cfg:setup"), context, deps)
    await callbacks.on_guided_text(make_text_update(ADDR), context, deps)
    assert "wizard_pending" in context.chat_data

    cancel_update = make_callback_update("cfg:wizard:cancel")
    await callbacks.on_cfg_callback(cancel_update, context, deps)
    assert "wizard_pending" not in context.chat_data
    cancel_update.callback_query.message.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_track_without_pending_is_noop(deps, db):
    context = make_context()
    update = make_callback_update("cfg:wizard:track")
    await callbacks.on_cfg_callback(update, context, deps)
    assert db.list_tokens(100) == []
    deps.listener.add_token.assert_not_awaited()


@pytest.mark.asyncio
async def test_wizard_private_chat_works(deps, db, monkeypatch):
    """The wizard also works in private chats (everyone is admin there)."""
    context = make_context(admin_status=ChatMemberStatus.MEMBER)
    mock_token(monkeypatch)
    update = make_callback_update("cfg:setup", chat_type="private")
    await callbacks.on_cfg_callback(update, context, deps)
    assert context.chat_data["awaiting"] == "wizard_token"
    text_update = make_text_update(ADDR, chat_type="private")
    await callbacks.on_guided_text(text_update, context, deps)
    assert context.chat_data["wizard_pending"]["address"] == ADDR
