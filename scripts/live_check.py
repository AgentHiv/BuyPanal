#!/usr/bin/env python3
"""Live validation against Monad mainnet (https://rpc.monad.xyz).

Usage:
    python scripts/live_check.py <token_address>

Prints, for the given token:
- ERC20 metadata and auto-detected kind
- price in MON and detected quote info
- bonding-curve (incubation) status
- the last 5 Transfer transactions classified as buy/sell/None with
  the quote-leg amount in MON (and USD when available)

This script is NOT part of the pytest suite; it performs real RPC calls.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from web3 import Web3  # noqa: E402

from chain.abis import TRANSFER_TOPIC  # noqa: E402
from chain.client import ensure_connected, get_w3  # noqa: E402
from chain.detector import _get, _hex, build_buy_event, build_sell_event  # noqa: E402
from chain.incubation import get_curve_info  # noqa: E402
from chain.price import get_price_mon, get_token_info  # noqa: E402

_SCAN_BLOCKS = 20_000
_LAST_TXS = 5


async def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    address = Web3.to_checksum_address(sys.argv[1].strip())

    w3 = get_w3()
    w3 = await ensure_connected(w3)
    latest = await w3.eth.get_block_number()
    print(f"connected to Monad RPC, latest block: {latest}")
    print(f"token: {address}")
    print("=" * 72)

    info = await get_token_info(address)
    print(f"name:         {info.name!r}")
    print(f"symbol:       {info.symbol!r}")
    print(f"decimals:     {info.decimals}")
    print(f"total_supply: {info.total_supply:,.4f}")
    print(f"kind:         {info.kind}")
    print("-" * 72)

    price = await get_price_mon(address)
    print(f"price (MON):  {price:.12g}")
    print("-" * 72)

    curve = await get_curve_info(address)
    print("curve info:")
    print(f"  is_incubating: {curve.is_incubating}")
    print(f"  graduated:     {curve.graduated}")
    print(f"  progress_pct:  {curve.progress_pct}")
    print(f"  mon_raised:    {curve.mon_raised}")
    print(f"  curve_address: {curve.curve_address}")
    print("-" * 72)

    # Recent Transfer txs, newest-first, in small windows (the public RPC
    # rejects large eth_getLogs ranges).
    from chain.client import iter_logs_windowed

    seen: set[str] = set()
    recent = []
    total_logs = 0
    async for chunk in iter_logs_windowed(
        w3,
        {"address": address, "topics": [TRANSFER_TOPIC]},
        max(0, latest - _SCAN_BLOCKS),
        latest,
        max_windows=60,
    ):
        total_logs += len(chunk)
        for log in reversed(chunk):
            txh = _hex(_get(log, "transactionHash") or _get(log, "transaction_hash"))
            if txh and txh not in seen:
                seen.add(txh)
                recent.append(log)
                if len(recent) >= _LAST_TXS:
                    break
        if len(recent) >= _LAST_TXS:
            break
    scanned_to = (
        int(_get(recent[-1], "blockNumber", _get(recent[-1], "block_number", 0)) or 0)
        if recent
        else latest
    )

    print(f"last {len(recent)} Transfer txs (of {total_logs} logs scanned back to block {scanned_to}):")
    for log in recent:
        txh = _hex(_get(log, "transactionHash") or _get(log, "transaction_hash"))
        buy = await build_buy_event(w3, log, info)
        if buy is not None:
            label = f"BUY  amount_mon={buy.amount_mon:.6g} amount_usd={buy.amount_usd}"
        else:
            sell = await build_sell_event(w3, log, info)
            if sell is not None:
                label = f"SELL amount_mon={sell.amount_mon:.6g} amount_usd={sell.amount_usd}"
            else:
                label = "None (not a buy/sell)"
        print(f"  {txh}  {label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
