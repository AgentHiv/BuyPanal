"""Tests for core.i18n (SPEC 5.4)."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import i18n  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCALES_DIR = REPO_ROOT / "locales"

REQUIRED_KEYS = {
    "welcome", "help",
    "token.added", "token.exists", "token.removed", "token.not_found",
    "token.list_empty", "token.list_header",
    "settings.updated", "settings.show",
    "language.set", "language.invalid",
    "emoji.set", "minbuy.set", "whale.set",
    "buy.title", "buy.spent", "buy.received", "buy.buyer", "buy.price",
    "buy.mcap", "buy.incubation", "buy.whale",
    "price.line",
    "error.invalid_address", "error.admin_only", "error.generic",
    "incubation.title", "incubation.progress", "incubation.graduated",
    "incubation.not_incubating",
    "stats.title", "stats.line",
    "leaderboard.title", "leaderboard.row", "leaderboard.empty",
    "about",
}


def _flatten(d, prefix=""):
    out = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, key + "."))
        else:
            out[key] = v
    return out


def _load_locale(lang):
    with (LOCALES_DIR / f"{lang}.json").open(encoding="utf-8") as fh:
        return _flatten(json.load(fh))


@pytest.fixture(autouse=True)
def fresh_cache():
    i18n.reload_locales()
    yield
    i18n.reload_locales()


# ---------------------------------------------------------------- locales
class TestLocaleFiles:
    def test_locale_files_exist(self):
        for lang in ("en", "es", "zh"):
            assert (LOCALES_DIR / f"{lang}.json").is_file(), lang

    def test_identical_key_sets(self):
        en = set(_load_locale("en"))
        assert set(_load_locale("es")) == en
        assert set(_load_locale("zh")) == en

    def test_required_keys_present(self):
        for lang in ("en", "es", "zh"):
            assert REQUIRED_KEYS <= set(_load_locale(lang)), lang

    def test_placeholders_coherent_across_languages(self):
        import re
        locales = {lang: _load_locale(lang) for lang in ("en", "es", "zh")}
        for key in locales["en"]:
            ph = {lang: set(re.findall(r"\{(\w+)\}", data[key]))
                  for lang, data in locales.items()}
            assert ph["en"] == ph["es"] == ph["zh"], key

    def test_no_empty_strings(self):
        for lang in ("en", "es", "zh"):
            for key, value in _load_locale(lang).items():
                assert value.strip(), f"{lang}:{key} is empty"


# ------------------------------------------------------------------- t()
class TestTranslate:
    def test_supported_langs_constant(self):
        assert i18n.SUPPORTED_LANGS == {"en": "English", "es": "Español", "zh": "中文"}

    def test_each_language_returns_its_own_text(self):
        assert i18n.t("en", "buy.title") == "BUY"
        assert i18n.t("es", "buy.title") == "COMPRA"
        assert i18n.t("zh", "buy.title") == "买入"

    def test_distinct_translations(self):
        texts = {i18n.t(lang, "welcome") for lang in ("en", "es", "zh")}
        assert len(texts) == 3

    def test_format_with_kwargs(self):
        out = i18n.t("en", "token.added", address="0xABC")
        assert "0xABC" in out
        assert "{address}" not in out

    def test_format_with_kwargs_other_languages(self):
        assert "0xABC" in i18n.t("es", "token.added", address="0xABC")
        assert "0xABC" in i18n.t("zh", "token.added", address="0xABC")

    def test_multiple_kwargs(self):
        out = i18n.t("en", "stats.line", symbol="MON", count=3,
                     volume_mon="12.5", volume_usd="$4.00")
        assert "MON" in out and "3" in out and "12.5" in out and "$4.00" in out
        assert "{" not in out

    def test_missing_key_returns_key_itself(self):
        assert i18n.t("en", "no.such.key") == "no.such.key"
        assert i18n.t("es", "no.such.key") == "no.such.key"

    def test_unknown_language_falls_back_to_english(self):
        assert i18n.t("fr", "buy.title") == i18n.t("en", "buy.title")
        assert i18n.t("", "welcome") == i18n.t("en", "welcome")

    def test_unknown_language_with_kwargs(self):
        out = i18n.t("de", "language.set", language="Deutsch")
        assert out == i18n.t("en", "language.set", language="Deutsch")
        assert "Deutsch" in out

    def test_key_missing_in_language_falls_back_to_english(self):
        # Simulate an es locale that lacks a key present in en.
        i18n._cache["es"] = {}
        assert i18n.t("es", "buy.title") == i18n.t("en", "buy.title")

    def test_missing_kwarg_leaves_placeholder_untouched(self):
        out = i18n.t("en", "token.added")  # no address kwarg
        assert "{address}" in out

    def test_extra_kwargs_are_ignored(self):
        out = i18n.t("en", "buy.title", unrelated=42)
        assert out == "BUY"

    def test_nested_and_flat_keys_both_work(self):
        i18n._cache["en"] = {"a": {"b": "nested"}, "c.d": "flat"}
        assert i18n.t("en", "a.b") == "nested"
        assert i18n.t("en", "c.d") == "flat"


class TestCache:
    def test_locales_are_cached(self):
        i18n.t("en", "welcome")
        assert "en" in i18n._cache
        data = i18n._cache["en"]
        i18n.t("en", "help")
        assert i18n._cache["en"] is data  # loaded once, not re-read

    def test_reload_clears_cache(self):
        i18n.t("en", "welcome")
        i18n.reload_locales()
        assert i18n._cache == {}
