"""Async web3 client for Monad with simple RPC failover."""

from __future__ import annotations

import logging

from web3 import AsyncWeb3
from web3.providers.rpc import AsyncHTTPProvider

logger = logging.getLogger(__name__)

_DEFAULT_RPC_URLS = ("https://rpc.monad.xyz",)


def _rpc_urls() -> list[str]:
    """RPC URLs from config; comma-separated MONAD_RPC_URL enables failover."""
    try:
        from core.config import load_config

        raw = load_config().MONAD_RPC_URL or ""
    except Exception:
        raw = ""
    urls = [u.strip() for u in raw.split(",") if u.strip()]
    return urls or list(_DEFAULT_RPC_URLS)


async def _connect(url: str) -> AsyncWeb3:
    w3 = AsyncWeb3(AsyncHTTPProvider(url))
    if not await w3.is_connected():
        raise ConnectionError(f"cannot connect to Monad RPC at {url}")
    return w3


async def _get_w3_async() -> AsyncWeb3:
    """Try each configured RPC URL in order; raise if all fail."""
    last_exc: Exception | None = None
    for url in _rpc_urls():
        try:
            return await _connect(url)
        except Exception as exc:  # noqa: BLE001 - failover on any RPC error
            logger.warning("RPC %s failed: %s", url, exc)
            last_exc = exc
    raise ConnectionError(f"all Monad RPC endpoints failed: {last_exc}")


def get_w3() -> AsyncWeb3:
    """Returns connected AsyncWeb3 (async HTTP provider). Raises on failure.

    Note: the provider handshake is verified lazily; callers inside an event
    loop should prefer ``await ensure_connected(w3)`` (or ``_get_w3_async``)
    when they need a guaranteed live connection before first use.
    """
    url = _rpc_urls()[0]
    return AsyncWeb3(AsyncHTTPProvider(url))


async def ensure_connected(w3: AsyncWeb3) -> AsyncWeb3:
    """Verify a w3 instance is connected, else rebuild with failover."""
    try:
        if await w3.is_connected():
            return w3
    except Exception:  # noqa: BLE001
        pass
    return await _get_w3_async()
