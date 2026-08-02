"""Tests for chain.detector.build_buy_event — fully mocked, no live RPC."""

from __future__ import annotations

import pytest

from core.models import TokenInfo

from chain.abis import (
    CURVE_BUY_TOPIC,
    CURVE_SELL_TOPIC,
    TRANSFER_TOPIC,
    UNIV2_SWAP_TOPIC,
    USDC_ADDRESS,
    USDT_ADDRESS,
)
from chain.detector import _PAIR_TOKENS_CACHE, build_buy_event, build_sell_event

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

TOKEN = "0x" + "11" * 20
PAIR = "0x" + "22" * 20
CURVE = "0x" + "44" * 20
BUYER = "0x" + "33" * 20
SELLER = "0x" + "55" * 20
TX_HASH = "0x" + "ab" * 32
BLOCK_NUMBER = 1234
TIMESTAMP = 1_700_000_000

E18 = 10**18


def pad_addr(addr: str) -> str:
    return "0x" + "0" * 24 + addr.lower().removeprefix("0x")


def words(*values: int) -> bytes:
    return b"".join(int(v).to_bytes(32, "big") for v in values)


def transfer_log(frm: str, to: str, amount: int) -> dict:
    return {
        "address": TOKEN,
        "topics": [TRANSFER_TOPIC, pad_addr(frm), pad_addr(to)],
        "data": words(amount),
        "transactionHash": TX_HASH,
        "blockNumber": BLOCK_NUMBER,
        "logIndex": 0,
    }


def univ2_swap_log(pair: str, a0_in: int, a1_in: int, a0_out: int, a1_out: int) -> dict:
    return {
        "address": pair,
        "topics": [UNIV2_SWAP_TOPIC, pad_addr(BUYER)],
        "data": words(a0_in, a1_in, a0_out, a1_out),
        "transactionHash": TX_HASH,
        "blockNumber": BLOCK_NUMBER,
        "logIndex": 1,
    }


def curve_buy_log(curve: str, token: str, amount_in: int, amount_out: int) -> dict:
    return {
        "address": curve,
        "topics": [CURVE_BUY_TOPIC, pad_addr(BUYER), pad_addr(token)],
        "data": words(amount_in, amount_out),
        "transactionHash": TX_HASH,
        "blockNumber": BLOCK_NUMBER,
        "logIndex": 1,
    }


def curve_buy_log_token_first(curve: str, token: str, amount_in: int, amount_out: int) -> dict:
    """Real nad.fun layout: indexed (token, to); data = (monIn, tokenOut)."""
    return {
        "address": curve,
        "topics": [CURVE_BUY_TOPIC, pad_addr(token), pad_addr(BUYER)],
        "data": words(amount_in, amount_out),
        "transactionHash": TX_HASH,
        "blockNumber": BLOCK_NUMBER,
        "logIndex": 1,
    }


def curve_sell_log_token_first(curve: str, token: str, amount_in: int, amount_out: int) -> dict:
    """Real nad.fun sell: indexed (token, seller); data = (tokenIn, monOut)."""
    return {
        "address": curve,
        "topics": [CURVE_SELL_TOPIC, pad_addr(token), pad_addr(SELLER)],
        "data": words(amount_in, amount_out),
        "transactionHash": TX_HASH,
        "blockNumber": BLOCK_NUMBER,
        "logIndex": 1,
    }


class FakeEth:
    """Minimal async stand-in for w3.eth."""

    def __init__(self, receipt=None, tx=None):
        self._receipt = receipt if receipt is not None else {"logs": []}
        self._tx = tx if tx is not None else {"value": 0}

    async def get_transaction_receipt(self, tx_hash):
        return self._receipt

    async def get_transaction(self, tx_hash):
        return self._tx

    async def get_block(self, block_number):
        return {"timestamp": TIMESTAMP}


class FakeW3:
    def __init__(self, receipt=None, tx=None, contracts=None):
        self.eth = FakeEth(receipt=receipt, tx=tx)
        self._contracts = {str(k).lower(): v for k, v in (contracts or {}).items()}
        self.eth._w3 = self

    def contract_for(self, address):
        return self._contracts[str(address).lower()]


class FakeCall:
    """Awaitable contract function call returning a fixed value."""

    def __init__(self, value):
        self._value = value

    async def call(self):
        return self._value


class FakeContract:
    """Stand-in for w3.eth.contract(...): fixed return values per function."""

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


# Wire contract() support into FakeEth (default: none -> lookup raises inside
# detector, which is caught and falls back to the legacy heuristic).
def _fake_contract(self, address=None, abi=None):
    w3 = getattr(self, "_w3", None)
    if w3 is None:
        raise ValueError("no contracts configured")
    return w3.contract_for(address)


FakeEth.contract = _fake_contract


@pytest.fixture(autouse=True)
def _clear_pair_cache():
    """Pair token0/token1 lookups are cached forever; isolate each test."""
    _PAIR_TOKENS_CACHE.clear()
    yield
    _PAIR_TOKENS_CACHE.clear()


@pytest.fixture
def token_info() -> TokenInfo:
    return TokenInfo(
        address=TOKEN,
        name="Test Token",
        symbol="TEST",
        decimals=18,
        total_supply=1_000_000_000.0,
        kind="unknown",
    )


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_univ2_buy(token_info):
    """Tokens leave a UniV2 pair + Swap event in the same tx -> BuyEvent."""
    receipt = {
        "logs": [
            # buyer sent 5 MON (WMON in), received 1000 tokens out
            univ2_swap_log(PAIR, 5 * E18, 0, 0, 1000 * E18),
            transfer_log(PAIR, BUYER, 1000 * E18),
        ]
    }
    w3 = FakeW3(receipt=receipt)
    buy = await build_buy_event(w3, transfer_log(PAIR, BUYER, 1000 * E18), token_info)

    assert buy is not None
    assert buy.kind == "dex"
    assert buy.buyer == BUYER
    assert buy.pair_address == PAIR
    assert buy.amount_token == pytest.approx(1000.0)
    assert buy.amount_mon == pytest.approx(5.0)
    assert buy.price_mon == pytest.approx(5.0 / 1000.0)
    assert buy.amount_usd is None  # MON_USD_PRICE defaults to 0
    assert buy.tx_hash == TX_HASH
    assert buy.block_number == BLOCK_NUMBER
    assert buy.timestamp == TIMESTAMP
    assert buy.token_symbol == "TEST"


@pytest.mark.asyncio
async def test_curve_buy(token_info):
    """Curve Buy event with matching indexed token -> BuyEvent(kind=curve)."""
    receipt = {
        "logs": [
            curve_buy_log(CURVE, TOKEN, 10 * E18, 2000 * E18),
            transfer_log(CURVE, BUYER, 2000 * E18),
        ]
    }
    w3 = FakeW3(receipt=receipt)
    buy = await build_buy_event(w3, transfer_log(CURVE, BUYER, 2000 * E18), token_info)

    assert buy is not None
    assert buy.kind == "curve"
    assert buy.pair_address == CURVE
    assert buy.amount_mon == pytest.approx(10.0)  # amountIn from the Buy event
    assert buy.amount_token == pytest.approx(2000.0)
    assert buy.price_mon == pytest.approx(10.0 / 2000.0)


@pytest.mark.asyncio
async def test_sell_returns_none(token_info):
    """Tokens sent INTO the pair (seller -> pair) is a sell -> None."""
    receipt = {
        "logs": [
            # seller sent 1000 tokens in, got 5 MON out
            univ2_swap_log(PAIR, 0, 1000 * E18, 5 * E18, 0),
            transfer_log(SELLER, PAIR, 1000 * E18),
        ]
    }
    w3 = FakeW3(receipt=receipt)
    buy = await build_buy_event(w3, transfer_log(SELLER, PAIR, 1000 * E18), token_info)
    assert buy is None


@pytest.mark.asyncio
async def test_plain_transfer_returns_none(token_info):
    """Wallet-to-wallet transfer with no swap/buy events in tx -> None."""
    receipt = {"logs": [transfer_log(SELLER, BUYER, 500 * E18)]}
    w3 = FakeW3(receipt=receipt)
    buy = await build_buy_event(w3, transfer_log(SELLER, BUYER, 500 * E18), token_info)
    assert buy is None


@pytest.mark.asyncio
async def test_mint_returns_none(token_info):
    """Mint from the zero address is not a buy -> None."""
    zero = "0x" + "00" * 20
    receipt = {"logs": [univ2_swap_log(PAIR, 5 * E18, 0, 0, 1000 * E18)]}
    w3 = FakeW3(receipt=receipt)
    buy = await build_buy_event(w3, transfer_log(zero, BUYER, 1000 * E18), token_info)
    assert buy is None


@pytest.mark.asyncio
async def test_curve_buy_wrong_token_returns_none(token_info):
    """Curve Buy event for a DIFFERENT token must not match -> None."""
    other_token = "0x" + "99" * 20
    receipt = {
        "logs": [
            curve_buy_log(CURVE, other_token, 10 * E18, 2000 * E18),
            transfer_log(CURVE, BUYER, 2000 * E18),
        ]
    }
    w3 = FakeW3(receipt=receipt)
    buy = await build_buy_event(w3, transfer_log(CURVE, BUYER, 2000 * E18), token_info)
    assert buy is None


@pytest.mark.asyncio
async def test_curve_buy_token_first_layout(token_info):
    """nad.fun real layout (indexed token in topic1) must also match.

    Regression: on-chain nad.fun curves index (token, to), not
    (sender, token) — e.g. tx 0xa0e07386... on Monad mainnet.
    """
    receipt = {
        "logs": [
            curve_buy_log_token_first(CURVE, TOKEN, 10 * E18, 2000 * E18),
            transfer_log(CURVE, BUYER, 2000 * E18),
        ]
    }
    w3 = FakeW3(receipt=receipt)
    buy = await build_buy_event(w3, transfer_log(CURVE, BUYER, 2000 * E18), token_info)

    assert buy is not None
    assert buy.kind == "curve"
    assert buy.pair_address == CURVE
    assert buy.amount_mon == pytest.approx(10.0)
    assert buy.amount_token == pytest.approx(2000.0)
    assert buy.price_mon == pytest.approx(10.0 / 2000.0)


@pytest.mark.asyncio
async def test_curve_buy_token_first_wrong_token_returns_none(token_info):
    """Token-first layout for a DIFFERENT token must not match -> None."""
    other_token = "0x" + "99" * 20
    receipt = {
        "logs": [
            curve_buy_log_token_first(CURVE, other_token, 10 * E18, 2000 * E18),
            transfer_log(CURVE, BUYER, 2000 * E18),
        ]
    }
    w3 = FakeW3(receipt=receipt)
    buy = await build_buy_event(w3, transfer_log(CURVE, BUYER, 2000 * E18), token_info)
    assert buy is None


@pytest.mark.asyncio
async def test_curve_sell_token_first_layout(token_info):
    """nad.fun real sell layout: indexed (token, seller); (tokenIn, monOut)."""
    receipt = {
        "logs": [
            curve_sell_log_token_first(CURVE, TOKEN, 2000 * E18, 10 * E18),
            transfer_log(SELLER, CURVE, 2000 * E18),
        ]
    }
    w3 = FakeW3(receipt=receipt)
    sell = await build_sell_event(w3, transfer_log(SELLER, CURVE, 2000 * E18), token_info)

    assert sell is not None
    assert sell.kind == "curve"
    assert sell.pair_address == CURVE
    assert sell.amount_mon == pytest.approx(10.0)  # monOut from the Sell event
    assert sell.amount_token == pytest.approx(2000.0)
    assert sell.price_mon == pytest.approx(10.0 / 2000.0)


@pytest.mark.asyncio
async def test_tx_value_fallback(token_info):
    """Swap with zero decoded amounts falls back to native tx.value."""
    receipt = {
        "logs": [
            univ2_swap_log(PAIR, 0, 0, 0, 1000 * E18),
            transfer_log(PAIR, BUYER, 1000 * E18),
        ]
    }
    w3 = FakeW3(receipt=receipt, tx={"value": 3 * E18})
    buy = await build_buy_event(w3, transfer_log(PAIR, BUYER, 1000 * E18), token_info)

    assert buy is not None
    assert buy.amount_mon == pytest.approx(3.0)
    assert buy.price_mon == pytest.approx(3.0 / 1000.0)


@pytest.mark.asyncio
async def test_rpc_failure_returns_none(token_info):
    """Receipt fetch blowing up must yield None, never raise."""

    class BrokenEth:
        async def get_transaction_receipt(self, tx_hash):
            raise ConnectionError("rpc down")

    class BrokenW3:
        eth = BrokenEth()

    buy = await build_buy_event(BrokenW3(), transfer_log(PAIR, BUYER, 1000 * E18), token_info)
    assert buy is None


@pytest.mark.asyncio
async def test_garbage_log_returns_none(token_info):
    """Malformed transfer log must yield None, never raise."""
    w3 = FakeW3()
    assert await build_buy_event(w3, {"topics": []}, token_info) is None
    assert await build_buy_event(w3, None, token_info) is None


# ---------------------------------------------------------------------------
# Quote-leg detection (token0/token1) — USDC/USDT pairs
# ---------------------------------------------------------------------------

PAIR_USDC = "0x" + "66" * 20
PAIR_USDT = "0x" + "77" * 20
PAIR_UNKNOWN_QUOTE = "0x" + "88" * 20
UNKNOWN_QUOTE = "0x" + "99" * 20

E6 = 10**6


def _no_usd_feed(monkeypatch):
    monkeypatch.delenv("MON_USD_PRICE", raising=False)


@pytest.mark.asyncio
async def test_univ2_buy_usdc_pair_no_usd_feed(token_info, monkeypatch):
    """Buy against a TOKEN/USDC pair, no MON_USD_PRICE configured.

    The quote leg is found via token0/token1 (token1 = USDC): amount_mon is
    0.0 (no USD->MON feed) but amount_usd is preserved. The legacy heuristic
    would have returned ~2.5e-10 MON, so 0.0 proves the precise path ran.
    """
    _no_usd_feed(monkeypatch)
    receipt = {
        "logs": [
            # buyer spent 250 USDC (token1 in), received 1000 tokens (token0 out)
            univ2_swap_log(PAIR_USDC, 0, 250 * E6, 1000 * E18, 0),
            transfer_log(PAIR_USDC, BUYER, 1000 * E18),
        ]
    }
    contracts = {
        PAIR_USDC: FakeContract(token0=TOKEN, token1=USDC_ADDRESS),
    }
    w3 = FakeW3(receipt=receipt, contracts=contracts)
    buy = await build_buy_event(w3, transfer_log(PAIR_USDC, BUYER, 1000 * E18), token_info)

    assert buy is not None
    assert buy.kind == "dex"
    assert buy.pair_address == PAIR_USDC
    assert buy.amount_token == pytest.approx(1000.0)
    assert buy.amount_mon == pytest.approx(0.0)
    assert buy.amount_usd == pytest.approx(250.0)
    assert buy.price_mon == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_univ2_buy_usdc_pair_with_usd_feed(token_info, monkeypatch):
    """Same USDC-pair buy with MON_USD_PRICE=2.5 -> 250 USD = 100 MON."""
    monkeypatch.setenv("MON_USD_PRICE", "2.5")
    receipt = {
        "logs": [
            univ2_swap_log(PAIR_USDC, 0, 250 * E6, 1000 * E18, 0),
            transfer_log(PAIR_USDC, BUYER, 1000 * E18),
        ]
    }
    contracts = {
        PAIR_USDC: FakeContract(token0=TOKEN, token1=USDC_ADDRESS),
    }
    w3 = FakeW3(receipt=receipt, contracts=contracts)
    buy = await build_buy_event(w3, transfer_log(PAIR_USDC, BUYER, 1000 * E18), token_info)

    assert buy is not None
    assert buy.amount_mon == pytest.approx(100.0)
    assert buy.amount_usd == pytest.approx(250.0)
    assert buy.price_mon == pytest.approx(100.0 / 1000.0)


@pytest.mark.asyncio
async def test_univ2_sell_usdt_pair(token_info, monkeypatch):
    """Sell against a TOKEN/USDT0 pair: quote leg out = 500 USDT.

    With MON_USD_PRICE=4.0 -> 500 USD = 125 MON received by the seller.
    """
    monkeypatch.setenv("MON_USD_PRICE", "4.0")
    receipt = {
        "logs": [
            # seller sent 2000 tokens (token0 in), received 500 USDT (token1 out)
            univ2_swap_log(PAIR_USDT, 2000 * E18, 0, 0, 500 * E6),
            transfer_log(SELLER, PAIR_USDT, 2000 * E18),
        ]
    }
    contracts = {
        PAIR_USDT: FakeContract(token0=TOKEN, token1=USDT_ADDRESS),
    }
    w3 = FakeW3(receipt=receipt, contracts=contracts)
    sell = await build_sell_event(w3, transfer_log(SELLER, PAIR_USDT, 2000 * E18), token_info)

    assert sell is not None
    assert sell.kind == "dex"
    assert sell.buyer == SELLER
    assert sell.pair_address == PAIR_USDT
    assert sell.amount_token == pytest.approx(2000.0)
    assert sell.amount_mon == pytest.approx(125.0)
    assert sell.amount_usd == pytest.approx(500.0)
    assert sell.price_mon == pytest.approx(125.0 / 2000.0)


@pytest.mark.asyncio
async def test_pair_tokens_cached(token_info, monkeypatch):
    """token0/token1 is queried once per pair address (cache hit on reuse)."""
    _no_usd_feed(monkeypatch)
    calls = {"n": 0}

    class CountingContract(FakeContract):
        def __getattr__(self, name):
            fn = super().__getattr__(name)
            if name in ("token0", "token1"):
                def wrapped(*args, **kwargs):
                    calls["n"] += 1
                    return fn(*args, **kwargs)
                return wrapped
            return fn

    receipt = {
        "logs": [
            univ2_swap_log(PAIR_USDC, 0, 250 * E6, 1000 * E18, 0),
            transfer_log(PAIR_USDC, BUYER, 1000 * E18),
        ]
    }
    contracts = {PAIR_USDC: CountingContract(token0=TOKEN, token1=USDC_ADDRESS)}
    w3 = FakeW3(receipt=receipt, contracts=contracts)
    buy = await build_buy_event(w3, transfer_log(PAIR_USDC, BUYER, 1000 * E18), token_info)
    assert buy is not None
    assert calls["n"] == 2  # token0 + token1, once

    # Second event on the same pair: cache hit, no further calls.
    buy2 = await build_buy_event(w3, transfer_log(PAIR_USDC, BUYER, 1000 * E18), token_info)
    assert buy2 is not None
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_unknown_quote_pair_falls_back_to_heuristic(token_info, monkeypatch):
    """Pair whose legs are not known quotes -> legacy max-leg heuristic."""
    _no_usd_feed(monkeypatch)
    receipt = {
        "logs": [
            # 5 (18-dec units) in on token1 side, 1000 tokens out on token0
            univ2_swap_log(PAIR_UNKNOWN_QUOTE, 0, 5 * E18, 1000 * E18, 0),
            transfer_log(PAIR_UNKNOWN_QUOTE, BUYER, 1000 * E18),
        ]
    }
    contracts = {
        PAIR_UNKNOWN_QUOTE: FakeContract(token0=TOKEN, token1=UNKNOWN_QUOTE),
    }
    w3 = FakeW3(receipt=receipt, contracts=contracts)
    buy = await build_buy_event(
        w3, transfer_log(PAIR_UNKNOWN_QUOTE, BUYER, 1000 * E18), token_info
    )

    assert buy is not None
    assert buy.amount_mon == pytest.approx(5.0)  # heuristic: larger input leg
    assert buy.amount_usd is None


@pytest.mark.asyncio
async def test_pair_lookup_failure_falls_back_to_heuristic(token_info, monkeypatch):
    """If token0/token1 cannot be read at all, the heuristic still applies."""
    _no_usd_feed(monkeypatch)
    receipt = {
        "logs": [
            univ2_swap_log(PAIR, 5 * E18, 0, 0, 1000 * E18),
            transfer_log(PAIR, BUYER, 1000 * E18),
        ]
    }
    # No contracts configured -> FakeEth.contract raises -> heuristic.
    w3 = FakeW3(receipt=receipt)
    buy = await build_buy_event(w3, transfer_log(PAIR, BUYER, 1000 * E18), token_info)

    assert buy is not None
    assert buy.amount_mon == pytest.approx(5.0)
