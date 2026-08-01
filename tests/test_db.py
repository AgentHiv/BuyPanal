"""Tests for core.db (SPEC 5.3)."""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.db import Database  # noqa: E402
from core.models import BuyEvent, GroupSettings  # noqa: E402

CHAT = -100123
OTHER_CHAT = -100999
TOKEN_A = "0xAaAaAaAaAaAaAaAaAaAaAaAaAaAaAaAaAaAaAaAa"
TOKEN_B = "0xbBbBBBBbbBBBbbbBbbBbbbbBBbBbbbbBbBbbBBbB"


def make_buy(token=TOKEN_A, buyer="0xBuyer1", amount_mon=10.0,
             amount_usd=5.0, tx_hash="0xtx1", timestamp=None):
    return BuyEvent(
        token_address=token,
        token_symbol="TOK",
        token_name="Token",
        buyer=buyer,
        amount_token=1000.0,
        amount_mon=amount_mon,
        amount_usd=amount_usd,
        price_mon=0.01,
        tx_hash=tx_hash,
        pair_address="0xPair",
        kind="dex",
        block_number=1,
        timestamp=int(time.time()) if timestamp is None else timestamp,
    )


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "bot.db"))
    yield database
    database.close()


# ------------------------------------------------------------------ init
class TestInit:
    def test_creates_parent_directory(self, tmp_path):
        path = tmp_path / "nested" / "deeper" / "bot.db"
        assert not path.parent.exists()
        database = Database(str(path))
        assert path.exists()
        database.close()

    def test_reopen_existing_db_keeps_data(self, tmp_path):
        path = str(tmp_path / "bot.db")
        d1 = Database(path)
        d1.add_token(CHAT, TOKEN_A)
        d1.close()
        d2 = Database(path)
        assert d2.list_tokens(CHAT) == [TOKEN_A.lower()]
        d2.close()


# -------------------------------------------------------------- settings
class TestSettings:
    def test_defaults_for_new_chat(self, db):
        s = db.get_settings(CHAT)
        assert s == GroupSettings(chat_id=CHAT)
        assert s.language == "en"
        assert s.buy_emoji == "🟢"
        assert s.whale_emoji == "🐋"
        assert s.min_buy_mon == 1.0
        assert s.whale_mon == 100.0
        assert s.emoji_step_mon == 10.0

    def test_save_and_get_roundtrip(self, db):
        s = GroupSettings(
            chat_id=CHAT, language="es", buy_emoji="🔥", whale_emoji="🦈",
            min_buy_mon=5.5, whale_mon=250.0, emoji_step_mon=2.0,
        )
        db.save_settings(s)
        assert db.get_settings(CHAT) == s

    def test_save_updates_existing(self, db):
        db.save_settings(GroupSettings(chat_id=CHAT, language="es"))
        db.save_settings(GroupSettings(chat_id=CHAT, language="zh", whale_mon=1.0))
        s = db.get_settings(CHAT)
        assert s.language == "zh"
        assert s.whale_mon == 1.0

    def test_settings_are_per_chat(self, db):
        db.save_settings(GroupSettings(chat_id=CHAT, language="es"))
        assert db.get_settings(OTHER_CHAT).language == "en"


# ---------------------------------------------------------------- tokens
class TestTokens:
    def test_add_token_returns_true(self, db):
        assert db.add_token(CHAT, TOKEN_A) is True

    def test_add_duplicate_returns_false(self, db):
        assert db.add_token(CHAT, TOKEN_A) is True
        assert db.add_token(CHAT, TOKEN_A) is False

    def test_add_is_case_insensitive(self, db):
        db.add_token(CHAT, TOKEN_A.lower())
        assert db.add_token(CHAT, TOKEN_A.upper()) is False

    def test_add_with_kind(self, db):
        assert db.add_token(CHAT, TOKEN_A, kind="curve") is True

    def test_same_token_different_chats(self, db):
        assert db.add_token(CHAT, TOKEN_A) is True
        assert db.add_token(OTHER_CHAT, TOKEN_A) is True

    def test_remove_token(self, db):
        db.add_token(CHAT, TOKEN_A)
        assert db.remove_token(CHAT, TOKEN_A) is True
        assert db.list_tokens(CHAT) == []

    def test_remove_missing_returns_false(self, db):
        assert db.remove_token(CHAT, TOKEN_A) is False

    def test_remove_is_case_insensitive(self, db):
        db.add_token(CHAT, TOKEN_A.lower())
        assert db.remove_token(CHAT, TOKEN_A.upper()) is True

    def test_remove_only_affects_own_chat(self, db):
        db.add_token(CHAT, TOKEN_A)
        db.add_token(OTHER_CHAT, TOKEN_A)
        db.remove_token(CHAT, TOKEN_A)
        assert db.list_tokens(OTHER_CHAT) == [TOKEN_A.lower()]

    def test_list_tokens(self, db):
        db.add_token(CHAT, TOKEN_A)
        db.add_token(CHAT, TOKEN_B)
        assert sorted(db.list_tokens(CHAT)) == sorted(
            [TOKEN_A.lower(), TOKEN_B.lower()]
        )

    def test_list_tokens_empty(self, db):
        assert db.list_tokens(CHAT) == []

    def test_all_tracked_tokens(self, db):
        db.add_token(CHAT, TOKEN_A)
        db.add_token(OTHER_CHAT, TOKEN_A)
        db.add_token(CHAT, TOKEN_B)
        result = db.all_tracked_tokens()
        assert set(result) == {TOKEN_A.lower(), TOKEN_B.lower()}
        assert sorted(result[TOKEN_A.lower()]) == sorted([CHAT, OTHER_CHAT])
        assert result[TOKEN_B.lower()] == [CHAT]

    def test_all_tracked_tokens_empty(self, db):
        assert db.all_tracked_tokens() == {}


# ----------------------------------------------------------------- stats
class TestStats:
    def test_stats_empty(self, db):
        assert db.get_stats_24h(CHAT) == {
            "count": 0, "volume_mon": 0.0, "volume_usd": 0.0,
        }

    def test_stats_aggregates(self, db):
        db.record_buy(CHAT, make_buy(amount_mon=10.0, amount_usd=4.0, tx_hash="0x1"))
        db.record_buy(CHAT, make_buy(amount_mon=2.5, amount_usd=1.0, tx_hash="0x2"))
        stats = db.get_stats_24h(CHAT)
        assert stats["count"] == 2
        assert stats["volume_mon"] == pytest.approx(12.5)
        assert stats["volume_usd"] == pytest.approx(5.0)

    def test_stats_none_usd_counts_as_zero(self, db):
        db.record_buy(CHAT, make_buy(amount_mon=3.0, amount_usd=None, tx_hash="0x1"))
        stats = db.get_stats_24h(CHAT)
        assert stats["count"] == 1
        assert stats["volume_mon"] == pytest.approx(3.0)
        assert stats["volume_usd"] == 0.0

    def test_stats_excludes_buys_older_than_24h(self, db):
        old_ts = int(time.time()) - 25 * 3600
        db.record_buy(CHAT, make_buy(amount_mon=99.0, tx_hash="0xold",
                                     timestamp=old_ts))
        db.record_buy(CHAT, make_buy(amount_mon=1.0, tx_hash="0xnew"))
        stats = db.get_stats_24h(CHAT)
        assert stats["count"] == 1
        assert stats["volume_mon"] == pytest.approx(1.0)

    def test_stats_token_filter(self, db):
        db.record_buy(CHAT, make_buy(token=TOKEN_A, amount_mon=10.0, tx_hash="0x1"))
        db.record_buy(CHAT, make_buy(token=TOKEN_B, amount_mon=5.0, tx_hash="0x2"))
        stats = db.get_stats_24h(CHAT, token=TOKEN_A)
        assert stats["count"] == 1
        assert stats["volume_mon"] == pytest.approx(10.0)

    def test_stats_token_filter_case_insensitive(self, db):
        db.record_buy(CHAT, make_buy(token=TOKEN_A, amount_mon=10.0, tx_hash="0x1"))
        stats = db.get_stats_24h(CHAT, token=TOKEN_A.upper())
        assert stats["count"] == 1

    def test_stats_are_per_chat(self, db):
        db.record_buy(CHAT, make_buy(amount_mon=10.0, tx_hash="0x1"))
        assert db.get_stats_24h(OTHER_CHAT)["count"] == 0


# ----------------------------------------------------------- leaderboard
class TestTopBuyers:
    def test_empty(self, db):
        assert db.get_top_buyers(CHAT) == []

    def test_aggregates_per_buyer_sorted_desc(self, db):
        db.record_buy(CHAT, make_buy(buyer="0xA", amount_mon=5.0, tx_hash="0x1"))
        db.record_buy(CHAT, make_buy(buyer="0xA", amount_mon=7.0, tx_hash="0x2"))
        db.record_buy(CHAT, make_buy(buyer="0xB", amount_mon=20.0, tx_hash="0x3"))
        db.record_buy(CHAT, make_buy(buyer="0xC", amount_mon=1.0, tx_hash="0x4"))
        top = db.get_top_buyers(CHAT)
        assert top == [("0xB", 20.0), ("0xA", 12.0), ("0xC", 1.0)]

    def test_limit(self, db):
        for i in range(5):
            db.record_buy(CHAT, make_buy(buyer=f"0xBuyer{i}", amount_mon=float(i),
                                         tx_hash=f"0x{i}"))
        top = db.get_top_buyers(CHAT, limit=3)
        assert len(top) == 3
        assert top[0][0] == "0xBuyer4"

    def test_default_limit_is_ten(self, db):
        for i in range(15):
            db.record_buy(CHAT, make_buy(buyer=f"0xBuyer{i}", amount_mon=float(i + 1),
                                         tx_hash=f"0x{i}"))
        assert len(db.get_top_buyers(CHAT)) == 10

    def test_token_filter(self, db):
        db.record_buy(CHAT, make_buy(token=TOKEN_A, buyer="0xA", amount_mon=5.0,
                                     tx_hash="0x1"))
        db.record_buy(CHAT, make_buy(token=TOKEN_B, buyer="0xB", amount_mon=50.0,
                                     tx_hash="0x2"))
        assert db.get_top_buyers(CHAT, token=TOKEN_A) == [("0xA", 5.0)]

    def test_per_chat_isolation(self, db):
        db.record_buy(CHAT, make_buy(buyer="0xA", amount_mon=5.0, tx_hash="0x1"))
        assert db.get_top_buyers(OTHER_CHAT) == []
