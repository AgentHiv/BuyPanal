"""Tests for core.db v2 (SPEC-v2 §2): price alerts, new settings fields,
v1->v2 migration and list_known_chats."""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.db import Database  # noqa: E402
from core.models import GroupSettings, PriceAlert  # noqa: E402

CHAT = -100123
OTHER_CHAT = -100999
TOKEN_A = "0xAaAaAaAaAaAaAaAaAaAaAaAaAaAaAaAaAaAaAaAa"
TOKEN_B = "0xbBbBBBBbbBBBbbbBbbBbbbbBBbBbbbbBbBbbBBbB"

# The v1 schema, exactly as shipped before v2 (no sell_alerts /
# scanner_alerts / sell_emoji columns, no price_alerts table).
_V1_SCHEMA = """
CREATE TABLE IF NOT EXISTS group_settings (
    chat_id        INTEGER PRIMARY KEY,
    language       TEXT    NOT NULL DEFAULT 'en',
    buy_emoji      TEXT    NOT NULL DEFAULT '🟢',
    whale_emoji    TEXT    NOT NULL DEFAULT '🐋',
    min_buy_mon    REAL    NOT NULL DEFAULT 1.0,
    whale_mon      REAL    NOT NULL DEFAULT 100.0,
    emoji_step_mon REAL    NOT NULL DEFAULT 10.0
);

CREATE TABLE IF NOT EXISTS tracked_tokens (
    chat_id  INTEGER NOT NULL,
    address  TEXT    NOT NULL,
    kind     TEXT    NOT NULL DEFAULT 'unknown',
    PRIMARY KEY (chat_id, address)
);

CREATE TABLE IF NOT EXISTS buys (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id       INTEGER NOT NULL,
    token_address TEXT    NOT NULL,
    buyer         TEXT    NOT NULL,
    amount_mon    REAL    NOT NULL DEFAULT 0.0,
    amount_usd    REAL,
    tx_hash       TEXT    NOT NULL,
    ts            INTEGER NOT NULL
);
"""


def _make_v1_db(path: str) -> None:
    """Create a database file with the ORIGINAL v1 schema + one row."""
    conn = sqlite3.connect(path)
    conn.executescript(_V1_SCHEMA)
    conn.execute(
        "INSERT INTO group_settings (chat_id, language, min_buy_mon)"
        " VALUES (?, 'es', 5.0)",
        (CHAT,),
    )
    conn.execute(
        "INSERT INTO tracked_tokens (chat_id, address, kind) VALUES (?, ?, 'dex')",
        (CHAT, TOKEN_A.lower()),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "bot.db"))
    yield database
    database.close()


# ------------------------------------------------------------ price alerts
class TestPriceAlerts:
    def test_add_returns_incrementing_ids(self, db):
        id1 = db.add_price_alert(CHAT, TOKEN_A, "above", 0.5, 42)
        id2 = db.add_price_alert(CHAT, TOKEN_A, "below", 0.1, 43)
        assert isinstance(id1, int)
        assert id2 > id1

    def test_list_returns_alerts_with_fields(self, db):
        alert_id = db.add_price_alert(CHAT, TOKEN_A, "above", 0.5, 42)
        alerts = db.list_price_alerts(CHAT)
        assert len(alerts) == 1
        alert = alerts[0]
        assert isinstance(alert, PriceAlert)
        assert alert.id == alert_id
        assert alert.chat_id == CHAT
        assert alert.token_address == TOKEN_A.lower()
        assert alert.direction == "above"
        assert alert.target_mon == pytest.approx(0.5)
        assert alert.created_by == 42
        assert alert.active is True

    def test_list_is_per_chat(self, db):
        db.add_price_alert(CHAT, TOKEN_A, "above", 0.5, 42)
        assert db.list_price_alerts(OTHER_CHAT) == []

    def test_address_is_normalized(self, db):
        db.add_price_alert(CHAT, TOKEN_A.upper(), "above", 0.5, 42)
        assert db.list_price_alerts(CHAT)[0].token_address == TOKEN_A.lower()

    def test_deactivate_hides_from_active_list(self, db):
        alert_id = db.add_price_alert(CHAT, TOKEN_A, "above", 0.5, 42)
        assert db.deactivate_price_alert(alert_id, CHAT) is True
        assert db.list_price_alerts(CHAT) == []
        # ... but still listed with active_only=False, marked inactive
        alerts = db.list_price_alerts(CHAT, active_only=False)
        assert len(alerts) == 1
        assert alerts[0].active is False

    def test_deactivate_twice_returns_false(self, db):
        alert_id = db.add_price_alert(CHAT, TOKEN_A, "above", 0.5, 42)
        assert db.deactivate_price_alert(alert_id, CHAT) is True
        assert db.deactivate_price_alert(alert_id, CHAT) is False

    def test_deactivate_scoped_to_chat(self, db):
        alert_id = db.add_price_alert(CHAT, TOKEN_A, "above", 0.5, 42)
        assert db.deactivate_price_alert(alert_id, OTHER_CHAT) is False
        assert len(db.list_price_alerts(CHAT)) == 1

    def test_deactivate_missing_returns_false(self, db):
        assert db.deactivate_price_alert(999, CHAT) is False

    def test_all_active_price_alerts(self, db):
        db.add_price_alert(CHAT, TOKEN_A, "above", 0.5, 42)
        db.add_price_alert(OTHER_CHAT, TOKEN_B, "below", 0.1, 43)
        inactive_id = db.add_price_alert(CHAT, TOKEN_B, "above", 1.0, 42)
        db.deactivate_price_alert(inactive_id, CHAT)
        alerts = db.all_active_price_alerts()
        assert len(alerts) == 2
        assert {a.chat_id for a in alerts} == {CHAT, OTHER_CHAT}

    def test_all_active_price_alerts_empty(self, db):
        assert db.all_active_price_alerts() == []


# --------------------------------------------------------- settings v2
class TestSettingsV2:
    def test_new_defaults(self, db):
        s = db.get_settings(CHAT)
        assert s.sell_alerts is False
        assert s.scanner_alerts is False
        assert s.sell_emoji == "🔴"

    def test_roundtrip_new_fields(self, db):
        s = GroupSettings(
            chat_id=CHAT,
            sell_alerts=True,
            scanner_alerts=True,
            sell_emoji="💔",
        )
        db.save_settings(s)
        loaded = db.get_settings(CHAT)
        assert loaded == s
        assert loaded.sell_alerts is True
        assert loaded.scanner_alerts is True
        assert loaded.sell_emoji == "💔"

    def test_update_preserves_new_fields(self, db):
        s = GroupSettings(chat_id=CHAT, sell_alerts=True, sell_emoji="💔")
        db.save_settings(s)
        s2 = db.get_settings(CHAT)
        s2.language = "zh"
        db.save_settings(s2)
        loaded = db.get_settings(CHAT)
        assert loaded.language == "zh"
        assert loaded.sell_alerts is True
        assert loaded.sell_emoji == "💔"


# ------------------------------------------------------------- migration
class TestMigrationFromV1:
    def test_open_v1_db_does_not_fail_and_migrates(self, tmp_path):
        path = str(tmp_path / "v1.db")
        _make_v1_db(path)
        db = Database(path)  # must never raise
        try:
            s = db.get_settings(CHAT)
            # v1 data preserved
            assert s.language == "es"
            assert s.min_buy_mon == pytest.approx(5.0)
            # new columns have defaults
            assert s.sell_alerts is False
            assert s.scanner_alerts is False
            assert s.sell_emoji == "🔴"
            assert db.list_tokens(CHAT) == [TOKEN_A.lower()]
        finally:
            db.close()

    def test_v1_db_can_save_new_fields(self, tmp_path):
        path = str(tmp_path / "v1.db")
        _make_v1_db(path)
        db = Database(path)
        s = db.get_settings(CHAT)
        s.sell_alerts = True
        s.scanner_alerts = True
        s.sell_emoji = "🔻"
        db.save_settings(s)
        db.close()
        # Reopen and verify persistence.
        db2 = Database(path)
        try:
            loaded = db2.get_settings(CHAT)
            assert loaded.sell_alerts is True
            assert loaded.scanner_alerts is True
            assert loaded.sell_emoji == "🔻"
            assert loaded.language == "es"
        finally:
            db2.close()

    def test_v1_db_price_alerts_work(self, tmp_path):
        path = str(tmp_path / "v1.db")
        _make_v1_db(path)
        db = Database(path)
        try:
            alert_id = db.add_price_alert(CHAT, TOKEN_A, "above", 0.5, 42)
            assert db.list_price_alerts(CHAT)[0].id == alert_id
        finally:
            db.close()

    def test_migration_is_idempotent(self, tmp_path):
        path = str(tmp_path / "v1.db")
        _make_v1_db(path)
        for _ in range(3):
            db = Database(path)  # opening repeatedly must never fail
            db.close()


# -------------------------------------------------------- known chats
class TestListKnownChats:
    def test_empty(self, db):
        assert db.list_known_chats() == []

    def test_chats_from_settings_only(self, db):
        db.save_settings(GroupSettings(chat_id=CHAT))
        assert db.list_known_chats() == [CHAT]

    def test_chats_from_tokens_only(self, db):
        db.add_token(OTHER_CHAT, TOKEN_A)
        assert db.list_known_chats() == [OTHER_CHAT]

    def test_union_deduplicates(self, db):
        db.save_settings(GroupSettings(chat_id=CHAT))
        db.add_token(CHAT, TOKEN_A)
        db.add_token(OTHER_CHAT, TOKEN_A)
        assert db.list_known_chats() == sorted([CHAT, OTHER_CHAT])
