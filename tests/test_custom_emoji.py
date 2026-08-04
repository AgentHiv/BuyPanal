"""Tests for Premium custom emoji support (bot.emojis + notifier entities +
guided capture in bot.callbacks). No live RPC or Telegram calls.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.config import Config
from core.models import BuyEvent, GroupSettings, SellEvent

from bot import callbacks, emojis, notifier


# ---------------------------------------------------------------------------
# bot.emojis helpers
# ---------------------------------------------------------------------------


def test_encode_decode_roundtrip():
    encoded = emojis.encode_custom_emoji("5289722755871162900", "🔥")
    assert encoded == "tg:5289722755871162900:🔥"
    assert emojis.decode_custom_emoji(encoded) == ("5289722755871162900", "🔥")


def test_decode_regular_emoji_returns_none():
    assert emojis.decode_custom_emoji("🟢") is None
    assert emojis.decode_custom_emoji("") is None
    assert emojis.decode_custom_emoji("tg:abc:🔥") is None  # non-numeric id
    assert emojis.decode_custom_emoji("tg:123") is None     # missing fallback


def test_encode_rejects_bad_input():
    with pytest.raises(ValueError):
        emojis.encode_custom_emoji("abc", "🔥")
    with pytest.raises(ValueError):
        emojis.encode_custom_emoji("123", "")


def test_display_emoji():
    assert emojis.display_emoji("tg:123:🔥") == "🔥"
    assert emojis.display_emoji("🟢") == "🟢"


def test_utf16_len():
    assert emojis.utf16_len("abc") == 3
    assert emojis.utf16_len("🔥") == 2  # surrogate pair = 2 UTF-16 units
    assert emojis.utf16_len("") == 0


def test_custom_emoji_from_entities():
    ent = SimpleNamespace(
        type="custom_emoji", offset=0, length=2, custom_emoji_id="5289722755871162900"
    )
    assert (
        emojis.custom_emoji_from_entities("🔥", [ent]) == "tg:5289722755871162900:🔥"
    )


def test_custom_emoji_from_entities_ignores_others():
    bold = SimpleNamespace(type="bold", offset=0, length=2, custom_emoji_id=None)
    assert emojis.custom_emoji_from_entities("🔥", [bold]) is None
    assert emojis.custom_emoji_from_entities("🔥", None) is None


# ---------------------------------------------------------------------------
# notifier: entities for custom emoji alerts
# ---------------------------------------------------------------------------


def make_buy(**overrides) -> BuyEvent:
    defaults = dict(
        token_address="0x" + "a1" * 20,
        token_symbol="TKN",
        token_name="Token",
        buyer="0x" + "b2" * 20,
        amount_token=10_000_000.0,
        amount_mon=50.0,
        amount_usd=250.0,
        price_mon=0.00000123,
        tx_hash="0x" + "ff" * 32,
        pair_address="0x" + "c3" * 20,
        kind="dex",
        block_number=1000,
        timestamp=1_700_000_000,
    )
    defaults.update(overrides)
    return BuyEvent(**defaults)


def make_sell(**overrides) -> SellEvent:
    defaults = dict(
        token_address="0x" + "a1" * 20,
        token_symbol="TKN",
        token_name="Token",
        buyer="0x" + "b2" * 20,
        amount_token=10_000_000.0,
        amount_mon=50.0,
        amount_usd=250.0,
        price_mon=0.00000123,
        tx_hash="0x" + "ff" * 32,
        pair_address="0x" + "c3" * 20,
        kind="dex",
        block_number=1000,
        timestamp=1_700_000_000,
    )
    defaults.update(overrides)
    return SellEvent(**defaults)


def make_settings(**overrides) -> GroupSettings:
    defaults = dict(chat_id=123)
    defaults.update(overrides)
    return GroupSettings(**defaults)


@pytest.fixture(autouse=True)
def inject_config():
    config = Config(TELEGRAM_TOKEN="test-token")
    config.EXPLORER_URL = "https://monadvision.com"
    config.BUY_URL_TEMPLATE = "https://nad.fun/token/{token}"
    notifier.set_config(config)
    yield
    notifier.set_config(None)


@pytest.fixture
def bot():
    return AsyncMock()


@pytest.mark.asyncio
async def test_custom_emoji_buy_alert_sends_entities(bot):
    # usd 250 / step 25 -> 10 repetitions
    settings = make_settings(buy_emoji="tg:5289722755871162900:🔥")
    await notifier.send_buy_alert(bot, 123, settings, make_buy(amount_usd=50.0), None)
    kwargs = bot.send_message.call_args.kwargs
    text = kwargs["text"]
    entities = kwargs["entities"]
    assert text.splitlines()[1] == "🔥🔥"  # 50 / 25 = 2
    assert entities is not None and len(entities) == 2
    title_len = emojis.utf16_len(text.splitlines()[0] + "\n")
    for i, ent in enumerate(entities):
        assert ent.type == "custom_emoji"
        assert ent.custom_emoji_id == "5289722755871162900"
        assert ent.offset == title_len + i * 2
        assert ent.length == 2


@pytest.mark.asyncio
async def test_custom_emoji_sell_alert_sends_entities(bot):
    settings = make_settings(sell_emoji="tg:999:🔴")
    await notifier.send_sell_alert(bot, 123, settings, make_sell(amount_usd=25.0), None)
    kwargs = bot.send_message.call_args.kwargs
    assert kwargs["text"].splitlines()[1] == "🔴"
    entities = kwargs["entities"]
    assert entities is not None and len(entities) == 1
    assert entities[0].custom_emoji_id == "999"


@pytest.mark.asyncio
async def test_regular_emoji_buy_alert_sends_no_entities(bot):
    settings = make_settings(buy_emoji="🟢")
    await notifier.send_buy_alert(bot, 123, settings, make_buy(amount_usd=50.0), None)
    kwargs = bot.send_message.call_args.kwargs
    assert kwargs["entities"] is None
    assert "🟢🟢" in kwargs["text"]


# ---------------------------------------------------------------------------
# callbacks: guided capture of a Premium custom emoji
# ---------------------------------------------------------------------------


class FakeDB:
    def __init__(self):
        self._settings = {}

    def get_settings(self, chat_id):
        if chat_id not in self._settings:
            self._settings[chat_id] = GroupSettings(chat_id=chat_id)
        return self._settings[chat_id]

    def save_settings(self, settings):
        self._settings[settings.chat_id] = settings


@pytest.mark.asyncio
async def test_guided_text_saves_custom_emoji():
    deps = SimpleNamespace(db=FakeDB())
    context = SimpleNamespace(
        chat_data={"awaiting": "buy_emoji", "awaiting_user": 1},
        bot=SimpleNamespace(
            get_chat_member=AsyncMock(
                return_value=SimpleNamespace(status="administrator")
            )
        ),
    )
    update = SimpleNamespace(
        callback_query=None,
        effective_chat=SimpleNamespace(id=100, type="group"),
        effective_user=SimpleNamespace(id=1),
        message=SimpleNamespace(
            text="🔥",
            entities=[
                SimpleNamespace(
                    type="custom_emoji",
                    offset=0,
                    length=2,
                    custom_emoji_id="5289722755871162900",
                )
            ],
            reply_text=AsyncMock(),
        ),
    )
    await callbacks.on_guided_text(update, context, deps)
    assert deps.db.get_settings(100).buy_emoji == "tg:5289722755871162900:🔥"
    assert "awaiting" not in context.chat_data
