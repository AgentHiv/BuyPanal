"""Helpers for Telegram Premium custom emoji support.

A custom emoji is stored inside the existing emoji settings fields encoded
as ``tg:<custom_emoji_id>:<fallback_char>`` (e.g. ``tg:5289722755871162900:🔥``).
Regular Unicode emojis are stored as-is, so every consumer must go through
``decode_custom_emoji`` / ``display_emoji`` instead of using the raw value.

Bot API entity offsets are counted in UTF-16 code units; ``utf16_len``
converts a Python string to that unit.
"""

from __future__ import annotations

CUSTOM_PREFIX = "tg:"


def encode_custom_emoji(custom_emoji_id: str, fallback: str) -> str:
    """Encode a custom emoji id + fallback char for storage."""
    custom_emoji_id = str(custom_emoji_id).strip()
    fallback = fallback.strip()
    if not custom_emoji_id.isdigit():
        raise ValueError(f"invalid custom_emoji_id: {custom_emoji_id!r}")
    if not fallback:
        raise ValueError("fallback must not be empty")
    return f"{CUSTOM_PREFIX}{custom_emoji_id}:{fallback}"


def decode_custom_emoji(value: str) -> "tuple[str, str] | None":
    """Split an encoded value into (custom_emoji_id, fallback), else None."""
    if not value or not value.startswith(CUSTOM_PREFIX):
        return None
    parts = value.split(":", 2)
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2]:
        return None
    return parts[1], parts[2]


def display_emoji(value: str) -> str:
    """Human-readable emoji for menus/replies (fallback for custom emoji)."""
    decoded = decode_custom_emoji(value)
    return decoded[1] if decoded else value


def utf16_len(text: str) -> int:
    """Length of ``text`` in UTF-16 code units (Bot API entity offsets)."""
    return len(text.encode("utf-16-le")) // 2


def custom_emoji_from_entities(text: str, entities) -> "str | None":
    """Encoded custom emoji from a message's entities, else None.

    ``entities`` is a sequence of telegram.MessageEntity; the entity type is
    compared as a plain string ("custom_emoji") so this module does not
    depend on the telegram package.
    """
    for ent in entities or ():
        if getattr(ent, "type", None) != "custom_emoji":
            continue
        custom_emoji_id = getattr(ent, "custom_emoji_id", None)
        if not custom_emoji_id:
            continue
        raw = text.encode("utf-16-le")
        fallback = raw[ent.offset * 2 : (ent.offset + ent.length) * 2].decode("utf-16-le")
        return encode_custom_emoji(str(custom_emoji_id), fallback)
    return None
