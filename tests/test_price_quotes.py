"""Tests for multi-quote pricing and kind auto-detection in chain.price.

Fully mocked: no live RPC. Pair/curve contract calls are faked.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.models import CurveInfo  # noqa: E402

from chain.abis import (  # noqa: E402
    TRANSFER_TOPIC,
    USDC_ADDRESS,
    WMON_ADDRESS,
)
from chain import price as chain_price  # noqa: E402

TOKEN = "0x" + "11" * 20
PAIR = "0x" + "22" * 20
SENDER = "0x" + "33" * 20

E18 = 10**18
E6 = 10**6


class FakeCall:
    def __init__(self, value):
        self._value = value

    async def call(self):
        return self._value


class FakeContract:
    def __init__(self, **values):
        self._values = values

    @property
    def functions(self):
        return self

    def __getattr__(self, name):
        try:
            value = object.__getattribute__(self, "_values")[name]
        except KeyError:
            raise AttributeError(name)
        return lambda *args, **kwargs: FakeCall(value)


class FakeEth:
    def __init__(self, contracts=None, logs=None, latest=1000):
        self._contracts = {str(k).lower(): v for k, v in (contracts or {}).items()}
        self._logs = logs or []
        self._latest = latest

    def contract(self, address=None, abi=None):
        return self._contracts[str(address).lower()]

    async def get_block_number(self):
        return self._latest

    async def get_logs(self, params):
        return self._logs


class FakeW3:
    def __init__(self, **kwargs):
        self.eth = FakeEth(**kwargs)


def pad_addr(addr: str) -> str:
    return "0x" + "0" * 24 + addr.lower().removeprefix("0x")


def transfer_from(sender: str) -> dict:
    return {"topics": [TRANSFER_TOPIC, pad_addr(sender), pad_addr(SENDER)], "data": b""}


def _curve_unknown(token_address: str) -> CurveInfo:
    return CurveInfo(
        token_address=token_address,
        is_incubating=False,
        progress_pct=None,
        mon_raised=None,
        graduated=False,
        curve_address=None,
    )


# ---------------------------------------------------------------------------
# Multi-quote pricing (_price_from_candidate)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_price_wmon_pair_unchanged(monkeypatch):
    """WMON pair: raw reserve ratio, exactly as before."""
    monkeypatch.delenv("MON_USD_PRICE", raising=False)
    contracts = {
        PAIR: FakeContract(
            getReserves=(5 * E18, 1000 * E18, 0),
            token0=WMON_ADDRESS,
            token1=TOKEN,
        ),
    }
    w3 = FakeW3(contracts=contracts)
    price = await chain_price._price_from_candidate(w3, PAIR, TOKEN)
    assert price == pytest.approx(5.0 / 1000.0)


@pytest.mark.asyncio
async def test_price_usdc_pair_with_usd_feed(monkeypatch):
    """USDC pair: USD price converted to MON via MON_USD_PRICE."""
    monkeypatch.setenv("MON_USD_PRICE", "2.0")
    contracts = {
        PAIR: FakeContract(
            # 400 USDC vs 1,000,000 tokens (18 dec) -> 0.0004 USD/token
            getReserves=(1_000_000 * E18, 400 * E6, 0),
            token0=TOKEN,
            token1=USDC_ADDRESS,
        ),
        TOKEN: FakeContract(decimals=18),
    }
    w3 = FakeW3(contracts=contracts)
    price = await chain_price._price_from_candidate(w3, PAIR, TOKEN)
    assert price == pytest.approx(0.0004 / 2.0)


@pytest.mark.asyncio
async def test_price_usdc_pair_without_usd_feed_returns_zero(monkeypatch):
    """USDC pair without MON_USD_PRICE cannot be priced in MON -> 0.0."""
    monkeypatch.delenv("MON_USD_PRICE", raising=False)
    contracts = {
        PAIR: FakeContract(
            getReserves=(1_000_000 * E18, 400 * E6, 0),
            token0=TOKEN,
            token1=USDC_ADDRESS,
        ),
        TOKEN: FakeContract(decimals=18),
    }
    w3 = FakeW3(contracts=contracts)
    price = await chain_price._price_from_candidate(w3, PAIR, TOKEN)
    assert price == 0.0


@pytest.mark.asyncio
async def test_price_unknown_pair_returns_zero(monkeypatch):
    """Pair against a non-quote token is still not priceable."""
    contracts = {
        PAIR: FakeContract(
            getReserves=(5 * E18, 1000 * E18, 0),
            token0="0x" + "99" * 20,
            token1=TOKEN,
        ),
    }
    w3 = FakeW3(contracts=contracts)
    price = await chain_price._price_from_candidate(w3, PAIR, TOKEN)
    assert price == 0.0


# ---------------------------------------------------------------------------
# Kind auto-detection (get_token_info)
# ---------------------------------------------------------------------------


def _patch_metadata(monkeypatch, contracts, logs=None):
    w3 = FakeW3(contracts=contracts, logs=logs)
    monkeypatch.setattr("chain.price.get_w3", lambda: w3)
    return w3


@pytest.mark.asyncio
async def test_kind_curve_when_incubating(monkeypatch):
    async def fake_curve_info(address):
        return CurveInfo(
            token_address=address,
            is_incubating=True,
            progress_pct=42.0,
            mon_raised=94_500.0,
            graduated=False,
            curve_address="0x" + "44" * 20,
        )

    monkeypatch.setattr("chain.incubation.get_curve_info", fake_curve_info)
    _patch_metadata(
        monkeypatch,
        {TOKEN: FakeContract(name="Curve Token", symbol="CRV", decimals=18, totalSupply=10**9 * E18)},
    )
    info = await chain_price.get_token_info(TOKEN)
    assert info.kind == "curve"
    assert info.symbol == "CRV"


@pytest.mark.asyncio
async def test_kind_dex_when_graduated(monkeypatch):
    async def fake_curve_info(address):
        return CurveInfo(
            token_address=address,
            is_incubating=False,
            progress_pct=100.0,
            mon_raised=225_000.0,
            graduated=True,
            curve_address="0x" + "44" * 20,
        )

    monkeypatch.setattr("chain.incubation.get_curve_info", fake_curve_info)
    _patch_metadata(monkeypatch, {TOKEN: FakeContract(name="T", symbol="T", decimals=18, totalSupply=0)})
    info = await chain_price.get_token_info(TOKEN)
    assert info.kind == "dex"


@pytest.mark.asyncio
async def test_kind_dex_when_known_univ2_pair(monkeypatch):
    """No curve found, but a recent sender is a (token, WMON) UniV2 pair."""
    monkeypatch.setattr(
        "chain.incubation.get_curve_info", _async_return_unknown
    )
    _patch_metadata(
        monkeypatch,
        {
            TOKEN: FakeContract(name="T", symbol="T", decimals=18, totalSupply=0),
            PAIR: FakeContract(token0=WMON_ADDRESS, token1=TOKEN),
        },
        logs=[transfer_from(PAIR)],
    )
    info = await chain_price.get_token_info(TOKEN)
    assert info.kind == "dex"


async def _async_return_unknown(address):
    return _curve_unknown(address)


@pytest.mark.asyncio
async def test_kind_unknown_when_nothing_found(monkeypatch):
    """Curve lookup fails and no pair exists -> 'unknown', never raises."""

    async def broken_curve_info(address):
        raise ConnectionError("rpc down")

    monkeypatch.setattr("chain.incubation.get_curve_info", broken_curve_info)
    _patch_metadata(monkeypatch, {TOKEN: FakeContract(name="T", symbol="T", decimals=18, totalSupply=0)}, logs=[])
    info = await chain_price.get_token_info(TOKEN)
    assert info.kind == "unknown"
