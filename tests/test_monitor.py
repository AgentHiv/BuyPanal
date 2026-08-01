"""Tests for chain.monitor.PriceMonitor (SPEC-v2 §5) — fully mocked."""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.models import PriceAlert  # noqa: E402

from chain.monitor import PriceMonitor  # noqa: E402

CHAT = -100123
TOKEN = "0x" + "aa" * 20
TOKEN_B = "0x" + "bb" * 20


def make_alert(alert_id=1, chat_id=CHAT, token=TOKEN, direction="above",
               target=1.0, active=True, target_usd=None, currency="MON") -> PriceAlert:
    return PriceAlert(
        id=alert_id,
        chat_id=chat_id,
        token_address=token,
        direction=direction,
        target_mon=target,
        created_by=42,
        active=active,
        target_usd=target_usd,
        currency=currency,
    )


@pytest.fixture
def fired():
    """List collecting (alert, price) tuples fired by the monitor."""
    return []


@pytest.fixture
def monitor(fired):
    async def on_alert(alert, price):
        fired.append((alert, price))

    return PriceMonitor(on_alert)


def patch_price(monkeypatch, prices):
    """Patch chain.monitor.get_price_mon with a static map (default 0.0)."""

    async def fake_get_price_mon(address):
        return float(prices.get(str(address).lower(), 0.0))

    monkeypatch.setattr("chain.monitor.get_price_mon", fake_get_price_mon)


# ------------------------------------------------------------------ triggers
@pytest.mark.asyncio
async def test_above_triggers_when_price_reaches_target(monkeypatch, monitor, fired):
    monitor.set_alert_provider(lambda: [make_alert(direction="above", target=1.0)])
    patch_price(monkeypatch, {TOKEN: 1.5})
    await monitor._check_once()
    assert len(fired) == 1
    alert, price = fired[0]
    assert alert.id == 1
    assert price == pytest.approx(1.5)


@pytest.mark.asyncio
async def test_above_does_not_trigger_below_target(monkeypatch, monitor, fired):
    monitor.set_alert_provider(lambda: [make_alert(direction="above", target=2.0)])
    patch_price(monkeypatch, {TOKEN: 1.5})
    await monitor._check_once()
    assert fired == []


@pytest.mark.asyncio
async def test_below_triggers_when_price_drops(monkeypatch, monitor, fired):
    monitor.set_alert_provider(lambda: [make_alert(direction="below", target=1.0)])
    patch_price(monkeypatch, {TOKEN: 0.5})
    await monitor._check_once()
    assert len(fired) == 1


@pytest.mark.asyncio
async def test_below_does_not_trigger_above_target(monkeypatch, monitor, fired):
    monitor.set_alert_provider(lambda: [make_alert(direction="below", target=0.4)])
    patch_price(monkeypatch, {TOKEN: 0.5})
    await monitor._check_once()
    assert fired == []


@pytest.mark.asyncio
async def test_zero_price_never_triggers_below(monkeypatch, monitor, fired):
    """0.0 means 'no liquidity / unknown' — must not fire false 'below'."""
    monitor.set_alert_provider(lambda: [make_alert(direction="below", target=1.0)])
    patch_price(monkeypatch, {TOKEN: 0.0})
    await monitor._check_once()
    assert fired == []


@pytest.mark.asyncio
async def test_inactive_alerts_are_skipped(monkeypatch, monitor, fired):
    monitor.set_alert_provider(lambda: [make_alert(active=False, target=0.1)])
    patch_price(monkeypatch, {TOKEN: 5.0})
    await monitor._check_once()
    assert fired == []


# ------------------------------------------------------------------ once-only
@pytest.mark.asyncio
async def test_alert_fires_only_once(monkeypatch, monitor, fired):
    """The bot layer deactivates after firing; the monitor also dedupes."""
    monitor.set_alert_provider(lambda: [make_alert(direction="above", target=1.0)])
    patch_price(monkeypatch, {TOKEN: 2.0})
    await monitor._check_once()
    await monitor._check_once()
    await monitor._check_once()
    assert len(fired) == 1


@pytest.mark.asyncio
async def test_different_alerts_same_token_each_fire(monkeypatch, monitor, fired):
    alerts = [
        make_alert(alert_id=1, direction="above", target=1.0),
        make_alert(alert_id=2, direction="above", target=1.5),
        make_alert(alert_id=3, direction="above", target=99.0),  # not reached
    ]
    monitor.set_alert_provider(lambda: alerts)
    calls = {"n": 0}

    async def counting_price(address):
        calls["n"] += 1
        return 2.0

    monkeypatch.setattr("chain.monitor.get_price_mon", counting_price)
    await monitor._check_once()
    assert {a.id for a, _ in fired} == {1, 2}
    # price fetched once per token, not once per alert
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_multiple_tokens(monkeypatch, monitor, fired):
    alerts = [
        make_alert(alert_id=1, token=TOKEN, direction="above", target=1.0),
        make_alert(alert_id=2, token=TOKEN_B, direction="below", target=1.0),
    ]
    monitor.set_alert_provider(lambda: alerts)
    patch_price(monkeypatch, {TOKEN: 2.0, TOKEN_B: 0.5})
    await monitor._check_once()
    assert {a.id for a, _ in fired} == {1, 2}


# ------------------------------------------------------------------ robustness
@pytest.mark.asyncio
async def test_no_provider_is_noop(monitor, fired):
    await monitor._check_once()
    assert fired == []


@pytest.mark.asyncio
async def test_provider_raising_does_not_propagate(monitor, fired):
    def broken_provider():
        raise RuntimeError("db down")

    monitor.set_alert_provider(broken_provider)
    await monitor._check_once()  # must not raise
    assert fired == []


@pytest.mark.asyncio
async def test_price_fetch_failure_does_not_propagate(monkeypatch, monitor, fired):
    monitor.set_alert_provider(lambda: [make_alert(direction="above", target=1.0)])

    async def broken_price(address):
        raise ConnectionError("rpc down")

    monkeypatch.setattr("chain.monitor.get_price_mon", broken_price)
    await monitor._check_once()  # must not raise
    assert fired == []


@pytest.mark.asyncio
async def test_callback_failure_still_dedupes(monkeypatch, fired):
    async def broken_on_alert(alert, price):
        raise RuntimeError("telegram down")

    monitor = PriceMonitor(broken_on_alert)
    monitor.set_alert_provider(lambda: [make_alert(direction="above", target=1.0)])
    patch_price(monkeypatch, {TOKEN: 2.0})
    await monitor._check_once()  # must not raise
    await monitor._check_once()
    assert fired == []  # broken callback: nothing recorded, and no refire loop


# ------------------------------------------------------------------ loop
@pytest.mark.asyncio
async def test_start_stop_loop(monkeypatch, fired):
    async def on_alert(alert, price):
        fired.append((alert, price))

    monitor = PriceMonitor(on_alert)
    monitor._interval = 0.02  # fast loop for the test
    monitor.set_alert_provider(lambda: [make_alert(direction="above", target=1.0)])
    patch_price(monkeypatch, {TOKEN: 2.0})

    task = asyncio.create_task(monitor.start())
    await asyncio.sleep(0.1)
    await monitor.stop()
    await asyncio.wait_for(task, timeout=2.0)
    assert len(fired) == 1  # fired exactly once despite several iterations


# ------------------------------------------------------------------ SPEC-v3 USD
@pytest.mark.asyncio
async def test_usd_alert_triggers_on_usd_value(monkeypatch, monitor, fired):
    """currency='USD': compares price_mon * mon_usd against target_usd."""
    monitor.set_price_provider(lambda: 0.02)  # 1 MON = $0.02
    monitor.set_alert_provider(
        lambda: [make_alert(direction="above", target=0.0, target_usd=2.0, currency="USD")]
    )
    patch_price(monkeypatch, {TOKEN: 150.0})  # 150 MON * 0.02 = $3.00 >= $2
    await monitor._check_once()
    assert len(fired) == 1
    alert, price = fired[0]
    assert alert.currency == "USD"
    assert price == pytest.approx(150.0)  # on_alert still receives price in MON


@pytest.mark.asyncio
async def test_usd_alert_does_not_trigger_below_usd_target(monkeypatch, monitor, fired):
    monitor.set_price_provider(lambda: 0.02)
    monitor.set_alert_provider(
        lambda: [make_alert(direction="above", target=0.0, target_usd=5.0, currency="USD")]
    )
    patch_price(monkeypatch, {TOKEN: 150.0})  # $3.00 < $5
    await monitor._check_once()
    assert fired == []


@pytest.mark.asyncio
async def test_usd_alert_never_fires_without_price_feed(monkeypatch, monitor, fired):
    """No set_price_provider -> mon_usd 0.0 -> USD alerts stay silent."""
    monitor.set_alert_provider(
        lambda: [make_alert(direction="below", target=0.0, target_usd=999.0, currency="USD")]
    )
    patch_price(monkeypatch, {TOKEN: 150.0})
    await monitor._check_once()
    assert fired == []


@pytest.mark.asyncio
async def test_mixed_mon_and_usd_alerts(monkeypatch, monitor, fired):
    """MON alerts keep v2 semantics while USD alerts use the price feed."""
    monitor.set_price_provider(lambda: 0.02)
    alerts = [
        # MON alert: above 100 MON (price 150 MON -> fires)
        make_alert(alert_id=1, direction="above", target=100.0),
        # USD alert: above $10 (150 * 0.02 = $3 -> no)
        make_alert(alert_id=2, direction="above", target=0.0, target_usd=10.0, currency="USD"),
        # USD alert: below $5 ($3 -> fires)
        make_alert(alert_id=3, direction="below", target=0.0, target_usd=5.0, currency="USD"),
        # MON alert: below 100 MON (150 -> no)
        make_alert(alert_id=4, direction="below", target=100.0),
    ]
    monitor.set_alert_provider(lambda: alerts)
    patch_price(monkeypatch, {TOKEN: 150.0})
    await monitor._check_once()
    assert {a.id for a, _ in fired} == {1, 3}
