"""Tests for chain.detector.build_buy_event — fully mocked, no live RPC."""

from __future__ import annotations

import pytest

from core.models import TokenInfo

from chain.abis import CURVE_BUY_TOPIC, TRANSFER_TOPIC, UNIV2_SWAP_TOPIC
from chain.detector import build_buy_event

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
    def __init__(self, receipt=None, tx=None):
        self.eth = FakeEth(receipt=receipt, tx=tx)


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
