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
CURVE_BUY_SIG = "Buy(address,address,uint256,uint256)"
CURVE_SELL_SIG = "Sell(address,address,uint256,uint256)"
CURVE_SYNC_SIG = "Sync(uint256,uint256)"


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

# Wrapped MON on Monad mainnet (quote currency on DEX pairs).
WMON_ADDRESS = "0x3bd359C1119dA7Da1D913D1C4D2B7c461115433A"

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
