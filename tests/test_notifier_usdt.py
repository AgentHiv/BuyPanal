"""Tests for SPEC-v3 §4: buy_value_usd and the USDT-based notifier paths
(mon_usd kwarg, USDT-first spent line, no-feed v2 fallback, sell alerts)."""

from unittest.mock import AsyncMock

import pytest

from core.config import Config
from core.models import BuyEvent, GroupSettings, SellEvent

from bot import notifier


def make_buy(**overrides) -> BuyEvent:
    defaults = dict(
        token_address="0x" + "a1" * 20,
        token_symbol="TKN",
        token_name="Token",
        buyer="0x" + "b2" * 20,
        amount_token=10_000_000.0,
        amount_mon=50.0,
        amount_usd=None,
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
        amount_usd=None,
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
    notifier.set_config(Config(TELEGRAM_TOKEN="test-token"))
    yield
    notifier.set_config(None)


@pytest.fixture
def bot():
    return AsyncMock()


def sent_text(bot) -> str:
    return bot.send_message.call_args.kwargs["text"]


# ---------------------------------------------------------------- buy_value_usd
class TestBuyValueUsd:
    def test_amount_usd_wins(self):
        assert notifier.buy_value_usd(50.0, 250.0, 0.02) == 250.0

    def test_amount_mon_times_mon_usd_when_no_usd(self):
        assert notifier.buy_value_usd(50.0, None, 0.02) == pytest.approx(1.0)

    def test_zero_without_price_feed(self):
        assert notifier.buy_value_usd(50.0, None, 0.0) == 0.0
        assert notifier.buy_value_usd(0.0, None, 0.02) == 0.0


# ------------------------------------------------------- mon_usd kwarg path
@pytest.mark.asyncio
async def test_mon_usd_kwarg_drives_usd_value(bot):
    """amount_usd None -> value derived from amount_mon * mon_usd."""
    settings = make_settings(min_buy_usdt=10.0, whale_usdt=100.0)
    # 50 MON * 0.02 = 1.0 USD < 10 USDT -> filtered out
    await notifier.send_buy_alert(
        bot, 123, settings, make_buy(amount_mon=50.0), None, mon_usd=0.02
    )
    bot.send_message.assert_not_called()

    # 10_000 MON * 0.02 = 200 USD >= 100 USDT -> whale
    await notifier.send_buy_alert(
        bot, 123, settings, make_buy(amount_mon=10_000.0), None, mon_usd=0.02
    )
    text = sent_text(bot)
    assert "200.00 USDT" in text


@pytest.mark.asyncio
async def test_spent_line_usdt_first(bot):
    settings = make_settings()
    await notifier.send_buy_alert(
        bot, 123, settings, make_buy(amount_mon=612.3, amount_usd=12.5), None
    )
    text = sent_text(bot)
    spent_line = next(ln for ln in text.splitlines() if "USDT" in ln)
    assert "12.50 USDT" in spent_line
    assert "612.30 MON" in spent_line  # MON shown in parentheses after USDT
    assert spent_line.index("USDT") < spent_line.index("MON")


@pytest.mark.asyncio
async def test_no_feed_shows_mon_only_and_single_emoji(bot):
    """usd == 0.0 -> v2 behaviour: sent, MON-only line, 1 emoji, no whale."""
    settings = make_settings(buy_emoji="🟢", whale_usdt=1.0, min_buy_usdt=9999.0)
    await notifier.send_buy_alert(
        bot, 123, settings, make_buy(amount_mon=50.0, amount_usd=None), None
    )
    text = sent_text(bot)
    assert text.splitlines()[0].startswith("🟢 ")
    spent_line = next(ln for ln in text.splitlines() if "50.00 MON" in ln)
    assert "USDT" not in spent_line
    assert "buy.whale" not in text  # no whale line without a price feed


@pytest.mark.asyncio
async def test_retrocompatible_call_without_mon_usd(bot):
    """v2 callers (positional only, no mon_usd kwarg) keep working."""
    settings = make_settings()
    await notifier.send_buy_alert(bot, 123, settings, make_buy(), None)
    bot.send_message.assert_called_once()


# ------------------------------------------------------------------ sell alerts
@pytest.mark.asyncio
async def test_sell_alert_usdt_filter_and_format(bot):
    settings = make_settings(min_buy_usdt=100.0, sell_emoji="🔴")
    # 50 MON * 0.02 = 1 USD < 100 USDT -> filtered
    await notifier.send_sell_alert(
        bot, 123, settings, make_sell(amount_mon=50.0), None, mon_usd=0.02
    )
    bot.send_message.assert_not_called()

    await notifier.send_sell_alert(
        bot, 123, settings, make_sell(amount_mon=10_000.0), None, mon_usd=0.02
    )
    text = sent_text(bot)
    assert "🔴" in text
    assert "200.00 USDT" in text
    assert "10,000.00 MON" in text


@pytest.mark.asyncio
async def test_sell_alert_retrocompatible(bot):
    settings = make_settings()
    await notifier.send_sell_alert(bot, 123, settings, make_sell(), None)
    bot.send_message.assert_called_once()
