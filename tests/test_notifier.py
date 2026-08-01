"""Tests for bot.notifier.send_buy_alert (chain/telegram mocked).

SPEC-v3 §4 changed thresholds/whale/emoji repetition from MON to USDT
(settings.*_usdt compared against the buy's USD value), so the tests below
use the *_usdt fields and USD amounts. Calls without the new ``mon_usd``
kwarg keep working (retro-compatible signature).
"""

from unittest.mock import AsyncMock

import pytest

from core.config import Config
from core.models import BuyEvent, CurveInfo, GroupSettings

from bot import notifier


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


def make_settings(**overrides) -> GroupSettings:
    defaults = dict(chat_id=123)
    defaults.update(overrides)
    return GroupSettings(**defaults)


def make_curve(**overrides) -> CurveInfo:
    defaults = dict(
        token_address="0x" + "a1" * 20,
        is_incubating=True,
        progress_pct=45.0,
        mon_raised=100_000.0,
        graduated=False,
        curve_address="0x" + "d4" * 20,
    )
    defaults.update(overrides)
    return CurveInfo(**defaults)


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


def sent_text(bot) -> str:
    return bot.send_message.call_args.kwargs["text"]


@pytest.mark.asyncio
async def test_min_buy_filter_skips_small_buys(bot):
    """SPEC-v3: the min-buy filter compares the USD value vs min_buy_usdt."""
    settings = make_settings(min_buy_usdt=100.0)
    await notifier.send_buy_alert(bot, 123, settings, make_buy(amount_usd=50.0), None)
    bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_min_buy_filter_sends_at_threshold(bot):
    settings = make_settings(min_buy_usdt=100.0)
    await notifier.send_buy_alert(bot, 123, settings, make_buy(amount_usd=100.0), None)
    bot.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_unknown_amount_zero_still_sends(bot):
    """No USD value (amount_usd None, no mon_usd feed) -> v2: still sent."""
    settings = make_settings(min_buy_usdt=10.0)
    await notifier.send_buy_alert(
        bot, 123, settings, make_buy(amount_mon=0.0, amount_usd=None), None
    )
    bot.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_whale_emoji_used_above_threshold(bot):
    settings = make_settings(whale_usdt=100.0, buy_emoji="🟢", whale_emoji="🐋")
    await notifier.send_buy_alert(
        bot, 123, settings, make_buy(amount_usd=150.0), None
    )
    text = sent_text(bot)
    assert "🐋" in text
    assert "🟢" not in text


@pytest.mark.asyncio
async def test_normal_buy_emoji_below_whale_threshold(bot):
    settings = make_settings(whale_usdt=100.0, buy_emoji="🟢", whale_emoji="🐋")
    await notifier.send_buy_alert(bot, 123, settings, make_buy(amount_usd=50.0), None)
    text = sent_text(bot)
    assert "🟢" in text
    assert "🐋" not in text


@pytest.mark.asyncio
async def test_whale_line_only_for_whales(bot):
    settings = make_settings(whale_usdt=100.0)
    await notifier.send_buy_alert(bot, 123, settings, make_buy(amount_usd=150.0), None)
    whale_text = sent_text(bot)
    assert "buy.whale" not in whale_text  # translated, key must not leak

    bot.reset_mock()
    await notifier.send_buy_alert(bot, 123, settings, make_buy(amount_usd=50.0), None)
    normal_text = sent_text(bot)
    assert len(normal_text.splitlines()) == len(whale_text.splitlines()) - 1


@pytest.mark.asyncio
async def test_emoji_repeat_count(bot):
    """1 emoji per emoji_step_usdt spent, min 1, capped at 20 (SPEC-v3)."""
    settings = make_settings(buy_emoji="🟢", emoji_step_usdt=10.0, whale_usdt=10000.0)
    await notifier.send_buy_alert(bot, 123, settings, make_buy(amount_usd=45.0), None)
    text = sent_text(bot)
    first_line = text.splitlines()[0]
    # int(45 / 10) == 4 -> four emojis on each side of the title
    assert "🟢🟢🟢🟢" in first_line
    assert "🟢🟢🟢🟢🟢" not in first_line


@pytest.mark.asyncio
async def test_emoji_repeat_minimum_one(bot):
    settings = make_settings(buy_emoji="🟢", emoji_step_usdt=10.0, whale_usdt=10000.0)
    await notifier.send_buy_alert(
        bot, 123, settings, make_buy(amount_mon=0.0, amount_usd=None), None
    )
    text = sent_text(bot)
    assert text.splitlines()[0].startswith("🟢 ")


@pytest.mark.asyncio
async def test_emoji_repeat_capped_at_20(bot):
    settings = make_settings(buy_emoji="🟢", emoji_step_usdt=10.0, whale_usdt=100000.0)
    await notifier.send_buy_alert(bot, 123, settings, make_buy(amount_usd=5000.0), None)
    text = sent_text(bot)
    first_line = text.splitlines()[0]
    assert first_line.startswith("🟢" * 20 + " ")
    assert "🟢" * 21 not in first_line


@pytest.mark.asyncio
async def test_incubation_line_present_when_incubating(bot):
    settings = make_settings()
    await notifier.send_buy_alert(
        bot, 123, settings, make_buy(kind="curve"), make_curve(is_incubating=True)
    )
    text = sent_text(bot)
    assert "45%" in text


@pytest.mark.asyncio
async def test_incubation_line_absent_when_not_incubating(bot):
    settings = make_settings()
    await notifier.send_buy_alert(
        bot, 123, settings, make_buy(kind="dex"), make_curve(is_incubating=False)
    )
    text_not_incubating = sent_text(bot)

    bot.reset_mock()
    await notifier.send_buy_alert(bot, 123, settings, make_buy(kind="dex"), None)
    text_no_curve = sent_text(bot)

    assert text_not_incubating == text_no_curve


@pytest.mark.asyncio
async def test_keyboard_has_tx_chart_buy_buttons(bot):
    settings = make_settings()
    buy = make_buy()
    await notifier.send_buy_alert(bot, 123, settings, buy, None)
    markup = bot.send_message.call_args.kwargs["reply_markup"]
    buttons = markup.inline_keyboard[0]
    labels = [b.text for b in buttons]
    assert labels == ["Tx", "Chart", "Buy"]
    urls = [b.url for b in buttons]
    assert urls[0] == f"https://monadvision.com/tx/{buy.tx_hash}"
    assert urls[1] == f"https://monadvision.com/token/{buy.token_address}"
    assert urls[2] == f"https://nad.fun/token/{buy.token_address}"


@pytest.mark.asyncio
async def test_usd_line_hidden_when_no_usd_feed(bot):
    settings = make_settings(whale_mon=1000.0)
    await notifier.send_buy_alert(
        bot, 123, settings, make_buy(amount_usd=None), None
    )
    text = sent_text(bot)
    spent_line = next(ln for ln in text.splitlines() if "50.00 MON" in ln)
    assert "($" not in spent_line
