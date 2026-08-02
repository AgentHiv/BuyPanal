"""Minimal ABIs and event topics for Monad (EVM) contracts.

Covers:
- ERC20 (name/symbol/decimals/totalSupply/balanceOf, Transfer event)
- Uniswap V2 style pair (getReserves, token0/token1, Swap event)
- Uniswap V3 style pool (Swap event)
- nad.fun style bonding curve (Buy/Sell/Sync events, getReserves/reserves)

Event topics are computed at runtime with ``Web3.keccak(text=...)`` —
no hashes are hardcoded.
"""

from web3 import Web3

# ---------------------------------------------------------------------------
# Event signatures (canonical text form)
# ---------------------------------------------------------------------------

ERC20_TRANSFER_SIG = "Transfer(address,address,uint256)"
UNIV2_SWAP_SIG = "Swap(address,uint256,uint256,uint256,uint256,address)"
UNIV3_SWAP_SIG = "Swap(address,address,int256,int256,uint160,uint128,int24)"
# nad.fun-style curve events. NOTE: the indexed-arg ORDER varies by
# deployment — the real nad.fun curve on Monad mainnet indexes
# (token, to), not (sender, token). Verified on-chain (chain ID 143), e.g.
# curve 0x9f3832732923252A21044F21eE6bd87F09514ae4, tx
# 0xa0e07386173ad893a6c29df301b362fb5aace080517bb0fad0d046eda8612d03:
#   Buy  topics = [sig, token, buyer],   data = (monIn, tokenOut)
#   Sell topics = [sig, token, seller],  data = (tokenIn, monOut)
CURVE_BUY_SIG = "Buy(address,address,uint256,uint256)"
CURVE_SELL_SIG = "Sell(address,address,uint256,uint256)"
CURVE_SYNC_SIG = "Sync(uint256,uint256)"
# Real Sync emitted by nad.fun curve clones (no getReserves()/reserves()
# getters exist on the EIP-1167 clones, so reserves come from this event):
#   Sync(address indexed token, uint256 vMon, uint256 realToken,
#        uint256 realMon, uint256 vToken)
CURVE_SYNC_FULL_SIG = "Sync(address,uint256,uint256,uint256,uint256)"


def event_topic(signature: str) -> str:
    """Return the 0x-prefixed keccak topic hash for an event signature."""
    return "0x" + Web3.keccak(text=signature).hex()


# Precomputed topics (runtime-computed, not hardcoded).
TRANSFER_TOPIC = event_topic(ERC20_TRANSFER_SIG)
UNIV2_SWAP_TOPIC = event_topic(UNIV2_SWAP_SIG)
UNIV3_SWAP_TOPIC = event_topic(UNIV3_SWAP_SIG)
CURVE_BUY_TOPIC = event_topic(CURVE_BUY_SIG)
CURVE_SELL_TOPIC = event_topic(CURVE_SELL_SIG)
CURVE_SYNC_TOPIC = event_topic(CURVE_SYNC_SIG)
CURVE_SYNC_FULL_TOPIC = event_topic(CURVE_SYNC_FULL_SIG)

# ---------------------------------------------------------------------------
# Minimal ABIs
# ---------------------------------------------------------------------------

ERC20_ABI = [
    {
        "constant": True,
        "inputs": [],
        "name": "name",
        "outputs": [{"name": "", "type": "string"}],
        "type": "function",
        "stateMutability": "view",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "symbol",
        "outputs": [{"name": "", "type": "string"}],
        "type": "function",
        "stateMutability": "view",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function",
        "stateMutability": "view",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "totalSupply",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
        "stateMutability": "view",
    },
    {
        "constant": True,
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
        "stateMutability": "view",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "from", "type": "address"},
            {"indexed": True, "name": "to", "type": "address"},
            {"indexed": False, "name": "value", "type": "uint256"},
        ],
        "name": "Transfer",
        "type": "event",
    },
]

UNIV2_PAIR_ABI = [
    {
        "constant": True,
        "inputs": [],
        "name": "getReserves",
        "outputs": [
            {"name": "_reserve0", "type": "uint112"},
            {"name": "_reserve1", "type": "uint112"},
            {"name": "_blockTimestampLast", "type": "uint32"},
        ],
        "type": "function",
        "stateMutability": "view",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "token0",
        "outputs": [{"name": "", "type": "address"}],
        "type": "function",
        "stateMutability": "view",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "token1",
        "outputs": [{"name": "", "type": "address"}],
        "type": "function",
        "stateMutability": "view",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "sender", "type": "address"},
            {"indexed": False, "name": "amount0In", "type": "uint256"},
            {"indexed": False, "name": "amount1In", "type": "uint256"},
            {"indexed": False, "name": "amount0Out", "type": "uint256"},
            {"indexed": False, "name": "amount1Out", "type": "uint256"},
            {"indexed": True, "name": "to", "type": "address"},
        ],
        "name": "Swap",
        "type": "event",
    },
]

UNIV3_POOL_ABI = [
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "sender", "type": "address"},
            {"indexed": True, "name": "recipient", "type": "address"},
            {"indexed": False, "name": "amount0", "type": "int256"},
            {"indexed": False, "name": "amount1", "type": "int256"},
            {"indexed": False, "name": "sqrtPriceX96", "type": "uint160"},
            {"indexed": False, "name": "liquidity", "type": "uint128"},
            {"indexed": False, "name": "tick", "type": "int24"},
        ],
        "name": "Swap",
        "type": "event",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "token0",
        "outputs": [{"name": "", "type": "address"}],
        "type": "function",
        "stateMutability": "view",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "token1",
        "outputs": [{"name": "", "type": "address"}],
        "type": "function",
        "stateMutability": "view",
    },
]

# nad.fun style bonding curve. Some deployments expose getReserves() like a
# UniV2 pair, others a reserves() tuple (real/virtual MON + token reserves).
CURVE_ABI = [
    {
        "constant": True,
        "inputs": [],
        "name": "getReserves",
        "outputs": [
            {"name": "_reserve0", "type": "uint112"},
            {"name": "_reserve1", "type": "uint112"},
            {"name": "_blockTimestampLast", "type": "uint32"},
        ],
        "type": "function",
        "stateMutability": "view",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "reserves",
        "outputs": [
            {"name": "reserveMon", "type": "uint256"},
            {"name": "reserveToken", "type": "uint256"},
        ],
        "type": "function",
        "stateMutability": "view",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "token0",
        "outputs": [{"name": "", "type": "address"}],
        "type": "function",
        "stateMutability": "view",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "token1",
        "outputs": [{"name": "", "type": "address"}],
        "type": "function",
        "stateMutability": "view",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "sender", "type": "address"},
            {"indexed": True, "name": "token", "type": "address"},
            {"indexed": False, "name": "amountIn", "type": "uint256"},
            {"indexed": False, "name": "amountOut", "type": "uint256"},
        ],
        "name": "Buy",
        "type": "event",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "sender", "type": "address"},
            {"indexed": True, "name": "token", "type": "address"},
            {"indexed": False, "name": "amountIn", "type": "uint256"},
            {"indexed": False, "name": "amountOut", "type": "uint256"},
        ],
        "name": "Sell",
        "type": "event",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": False, "name": "reserveMon", "type": "uint256"},
            {"indexed": False, "name": "reserveToken", "type": "uint256"},
        ],
        "name": "Sync",
        "type": "event",
    },
]

def _abi_signature(entry: dict) -> tuple:
    """Collision key for an ABI entry (type, name, input types)."""
    inputs = ",".join(i.get("type", "") for i in entry.get("inputs", []))
    return (entry.get("type"), entry.get("name"), inputs)


def combine_abis(*abis: list) -> list:
    """Concatenate ABIs dropping duplicate function/event signatures.

    web3 v7 rejects contract ABIs with colliding selectors, so a naive
    ``ABI_A + ABI_B`` raises when both define e.g. ``getReserves()``.
    """
    seen: set = set()
    combined: list = []
    for abi in abis:
        for entry in abi:
            sig = _abi_signature(entry)
            if sig in seen:
                continue
            seen.add(sig)
            combined.append(entry)
    return combined


# UniV2 pair ABI extended with curve-only extras (reserves(), curve events),
# safe to pass to w3.eth.contract.
PAIR_OR_CURVE_ABI = combine_abis(UNIV2_PAIR_ABI, CURVE_ABI)


# Wrapped MON on Monad mainnet (quote currency on DEX pairs).
WMON_ADDRESS = "0x3bd359C1119dA7Da1D913D1C4D2B7c461115433A"

# Well-known quote tokens on Monad mainnet (chain ID 143), verified on-chain
# against https://rpc.monad.xyz (symbol()/decimals() calls):
# - USDC:  symbol() -> "USDC",  decimals() -> 6  (native Circle USDC)
# - USDT0: symbol() -> "USDT0", decimals() -> 6  (Tether USD, omnichain)
# - WETH:  symbol() -> "WETH",  decimals() -> 18 (Monad official token list)
USDC_ADDRESS = "0x754704Bc059F8C67012fEd69BC8A327a5aafb603"
USDT_ADDRESS = "0xe7cd86e13AC4309349F30B3435a9d337750fC82D"
WETH_ADDRESS = "0xEE8c0E9f1BFFb4Eb878d8f15f368A02a35481242"

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

# ---------------------------------------------------------------------------
# WMON/USDC pair (SPEC-v3 §3) — used to price MON in USD on-chain.
#
# Verified on-chain against https://rpc.monad.xyz (chain ID 143) on the
# PancakeSwap v2-style factory 0x02a84c1b3BBD7401a5f7fa98a384EBC70bB5749E:
#   factory.getPair(WMON, USDC) -> 0x27AA322b3f8Ba9d0041Df99c33fE4f3CC135E054
#   pair.token0() -> 0x3bd359C1119dA7Da1D913D1C4D2B7c461115433A (WMON, 18 dec)
#   pair.token1() -> 0x754704Bc059F8C67012fEd69BC8A327a5aafb603 (USDC, 6 dec)
#   getReserves()  -> ~16.77 WMON / ~0.3479 USDC  =>  MON ≈ $0.0207
# (token symbols/decimals confirmed via ERC20 symbol()/decimals() calls.)
# ---------------------------------------------------------------------------
PANCAKESWAP_V2_FACTORY = "0x02a84c1b3BBD7401a5f7fa98a384EBC70bB5749E"
WMON_USDC_PAIR = "0x27AA322b3f8Ba9d0041Df99c33fE4f3CC135E054"
