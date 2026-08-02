"""Regression tests for BuyListener chat-resolution address casing.

The db stores token addresses lowercase (``Database._norm``) while
``_handle_transfer`` checksums the log address before resolving chats.
The resolver must therefore match a checksum address against
lowercase-keyed storage — otherwise no chat is ever notified.
"""

from __future__ import annotations

from chain.listener import BuyListener

TOKEN_LC = "0x2e2e44e7fa6178822d4397299f719e89d1a67777"
TOKEN_CHECKSUM = "0x2E2e44E7FA6178822D4397299F719e89d1a67777"
CHAT_ID = -1003929814336


def _lowercase_keyed_resolver(address: str) -> list[int]:
    """Mimics db.all_tracked_tokens().get(address, []): lowercase keys."""
    return {TOKEN_LC: [CHAT_ID]}.get(address, [])


def test_resolve_chats_with_checksum_address():
    listener = BuyListener(on_buy=None)
    listener.set_chat_resolver(_lowercase_keyed_resolver)
    assert listener._resolve_chats(TOKEN_CHECKSUM) == [CHAT_ID]


def test_resolve_chats_with_lowercase_address():
    listener = BuyListener(on_buy=None)
    listener.set_chat_resolver(_lowercase_keyed_resolver)
    assert listener._resolve_chats(TOKEN_LC) == [CHAT_ID]


def test_resolve_chats_unknown_token():
    listener = BuyListener(on_buy=None)
    listener.set_chat_resolver(_lowercase_keyed_resolver)
    assert listener._resolve_chats("0x" + "11" * 20) == []
