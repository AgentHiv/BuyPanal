"""Tests for chain.monprice.get_mon_usd_price (SPEC-v3 §3) — fully mocked.

Covers: reserves -> price with the 18/6 decimal adjustment (both token
orders), failure -> 0.0 (never raises), the 60s in-memory cache and the
Config.MON_USD_PRICE manual override.
"""

import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chain import monprice  # noqa: E402
from chain.abis import USDC_ADDRESS, WMON_ADDRESS, WMON_USDC_PAIR  # noqa: E402


@pytest.fixture(autouse=True)
def clear_cache():
    monprice._clear_cache()
    yield
    monprice._clear_cache()


def make_w3(reserve0, reserve1, token0=WMON_ADDRESS, token1=USDC_ADDRESS,
            fail=False, calls=None):
    """Fake AsyncWeb3 whose pair contract returns canned reserves/tokens."""
    get_reserves_call = AsyncMock(return_value=(reserve0, reserve1, 1_700_000_000))
    if fail:
        get_reserves_call = AsyncMock(side_effect=ConnectionError("rpc down"))
    functions = SimpleNamespace(
        getReserves=lambda: SimpleNamespace(call=get_reserves_call),
        token0=lambda: SimpleNamespace(call=AsyncMock(return_value=token0)),
        token1=lambda: SimpleNamespace(call=AsyncMock(return_value=token1)),
    )

    def contract(address=None, abi=None):
        if calls is not None:
            calls.append(address)
        return SimpleNamespace(functions=functions)

    return SimpleNamespace(eth=SimpleNamespace(contract=contract))


@pytest.mark.asyncio
async def test_reserves_to_price_wmon_token0():
    # 20 WMON (18 dec) against 0.5 USDC (6 dec) -> $0.025
    w3 = make_w3(reserve0=20 * 10**18, reserve1=500_000)
    price = await monprice.get_mon_usd_price(w3)
    assert price == pytest.approx(0.025)


@pytest.mark.asyncio
async def test_reserves_to_price_usdc_token0():
    # Same pool mirrored: token0 = USDC, token1 = WMON.
    w3 = make_w3(
        reserve0=500_000, reserve1=20 * 10**18,
        token0=USDC_ADDRESS, token1=WMON_ADDRESS,
    )
    price = await monprice.get_mon_usd_price(w3)
    assert price == pytest.approx(0.025)


@pytest.mark.asyncio
async def test_uses_verified_pair_address():
    calls = []
    w3 = make_w3(reserve0=10**18, reserve1=20_000, calls=calls)
    await monprice.get_mon_usd_price(w3)
    assert calls and calls[0].lower() == WMON_USDC_PAIR.lower()


@pytest.mark.asyncio
async def test_failure_returns_zero_and_never_raises(monkeypatch):
    w3 = make_w3(0, 0, fail=True)
    # Disable the Sync-log discovery fallback so the failure path is direct.
    monkeypatch.setattr(
        monprice, "_discover_pair_via_sync_logs", AsyncMock(return_value=None)
    )
    price = await monprice.get_mon_usd_price(w3)
    assert price == 0.0


@pytest.mark.asyncio
async def test_empty_reserves_return_zero(monkeypatch):
    w3 = make_w3(reserve0=0, reserve1=0)
    monkeypatch.setattr(
        monprice, "_discover_pair_via_sync_logs", AsyncMock(return_value=None)
    )
    assert await monprice.get_mon_usd_price(w3) == 0.0


@pytest.mark.asyncio
async def test_cache_avoids_second_call():
    w3 = make_w3(reserve0=20 * 10**18, reserve1=500_000)
    first = await monprice.get_mon_usd_price(w3)
    # Break the mock: a second on-chain read would fail; the cache must win.
    w3_broken = make_w3(0, 0, fail=True)
    second = await monprice.get_mon_usd_price(w3_broken)
    assert first == pytest.approx(0.025)
    assert second == first


@pytest.mark.asyncio
async def test_cache_expires(monkeypatch):
    w3 = make_w3(reserve0=20 * 10**18, reserve1=500_000)
    await monprice.get_mon_usd_price(w3)
    # Age the cache beyond the TTL and change the reserves.
    monprice._cache_ts = time.monotonic() - (monprice.CACHE_TTL + 1)
    w3_new = make_w3(reserve0=10 * 10**18, reserve1=500_000)
    price = await monprice.get_mon_usd_price(w3_new)
    assert price == pytest.approx(0.05)


@pytest.mark.asyncio
async def test_manual_override_skips_onchain(monkeypatch):
    monkeypatch.setenv("MON_USD_PRICE", "0.03")
    w3 = make_w3(0, 0, fail=True)  # would fail if called
    price = await monprice.get_mon_usd_price(w3)
    assert price == pytest.approx(0.03)
