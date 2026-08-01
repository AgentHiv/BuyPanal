"""Translation loader for the Monad buy bot (SPEC 5.4 + SPEC-v2 §3).

Locale files live in ``locales/<lang>.json`` at the repository root and are
loaded once and cached. Keys may be nested (``{"buy": {"title": ...}}``) and
are referenced with dot notation (``"buy.title"``). Flat keys containing
dots are supported as well.

SPEC-v2: extra files (``locales/<lang>.adv.json``, etc.) are deep-merged on
top of the base file, so feature modules can ship their own key files
without touching the base locales. The ``t()`` signature is unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SUPPORTED_LANGS = {"en": "English", "es": "Español", "zh": "中文"}

LOCALES_DIR = Path(__file__).resolve().parent.parent / "locales"
DEFAULT_LANG = "en"

# Extra locale file suffixes merged (in order) over "<lang>.json".
_EXTRA_SUFFIXES = (".adv",)

_cache: dict[str, dict[str, Any]] = {}


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Merge ``overlay`` into ``base`` recursively (overlay wins)."""
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def _read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _load(lang: str) -> dict[str, Any]:
    """Load and cache the merged locale dict for ``lang`` ({} if missing)."""
    if lang in _cache:
        return _cache[lang]
    data: dict[str, Any] = _read_json(LOCALES_DIR / f"{lang}.json")
    for suffix in _EXTRA_SUFFIXES:
        extra = _read_json(LOCALES_DIR / f"{lang}{suffix}.json")
        if extra:
            _deep_merge(data, extra)
    _cache[lang] = data
    return data


def reload_locales() -> None:
    """Clear the locale cache (useful for tests or hot-reloading files)."""
    _cache.clear()


def _lookup(lang: str, key: str) -> str | None:
    """Find ``key`` in the locale dict for ``lang``; None if missing."""
    data = _load(lang)
    # Nested lookup via dot notation.
    node: Any = data
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            node = None
            break
        node = node[part]
    if isinstance(node, str):
        return node
    # Flat key containing dots (e.g. {"buy.title": "..."}).
    node = data.get(key)
    if isinstance(node, str):
        return node
    return None


class _SafeDict(dict):
    """dict whose missing keys render as the original placeholder."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _safe_format(text: str, kwargs: dict[str, Any]) -> str:
    if not kwargs:
        return text
    try:
        return text.format_map(_SafeDict(kwargs))
    except (ValueError, IndexError, KeyError):
        # Malformed template (e.g. unbalanced braces): return it raw.
        return text


def t(lang: str, key: str, **kwargs: Any) -> str:
    """Translate ``key`` into ``lang`` (fallback: en). Formats with kwargs.

    - Unknown/unsupported language -> falls back to English.
    - Missing key in the chosen language -> falls back to English.
    - Missing key everywhere -> returns the key itself.
    - Missing format kwargs leave their placeholders untouched.
    """
    if lang not in SUPPORTED_LANGS:
        lang = DEFAULT_LANG
    text = _lookup(lang, key)
    if text is None and lang != DEFAULT_LANG:
        text = _lookup(DEFAULT_LANG, key)
    if text is None:
        return key
    return _safe_format(text, kwargs)
