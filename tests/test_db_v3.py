"""Tests for core.db v3 (SPEC-v3 §2): USDT settings fields, USD price
alerts and the tolerant v2 -> v3 migration from a real v2 database file."""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.db import Database  # noqa: E402
from core.models import GroupSettings, PriceAlert  # noqa: E402

CHAT = -100123
TOKEN_A = "0xAaAaAaAaAaAaAaAaAaAaAaAaAaAaAaAaAaAaAaAa"

# The v2 schema exactly as shipped before v3: group_settings WITH the v2
# columns and the price_alerts table WITHOUT target_usd/currency.
_V2_SCHEMA = """
CREATE TABLE IF NOT EXISTS group_settings (
    chat_id        INTEGER PRIMARY KEY,
    language       TEXT    NOT NULL DEFAULT 'en',
    buy_emoji      TEXT    NOT NULL DEFAULT '🟢',
    whale_emoji    TEXT    NOT NULL DEFAULT '🐋',
    min_buy_mon    REAL    NOT NULL DEFAULT 1.0,
    whale_mon      REAL    NOT NULL DEFAULT 100.0,
    emoji_step_mon REAL    NOT NULL DEFAULT 10.0,
    sell_alerts    INTEGER NOT NULL DEFAULT 0,
    scanner_alerts INTEGER NOT NULL DEFAULT 0,
    sell_emoji     TEXT    NOT NULL DEFAULT '🔴'
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

CREATE TABLE IF NOT EXISTS price_alerts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id       INTEGER NOT NULL,
    token_address TEXT    NOT NULL,
    direction     TEXT    NOT NULL,
    target_mon    REAL    NOT NULL,
    created_by    INTEGER NOT NULL,
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    INTEGER NOT NULL DEFAULT 0
);
"""


def _make_v2_db(path: str) -> None:
    """Create a database file with the v2 schema, one settings row and one
    legacy MON price alert."""
    conn = sqlite3.connect(path)
    conn.executescript(_V2_SCHEMA)
    conn.execute(
        "INSERT INTO group_settings (chat_id, language, min_buy_mon, sell_alerts)"
        " VALUES (?, 'es', 7.0, 1)",
        (CHAT,),
    )
    conn.execute(
        "INSERT INTO price_alerts (chat_id, token_address, direction,"
        " target_mon, created_by, active, created_at)"
        " VALUES (?, ?, 'above', 0.5, 42, 1, 1700000000)",
        (CHAT, TOKEN_A.lower()),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "bot.db"))
    yield database
    database.close()


# --------------------------------------------------------- settings v3
class TestSettingsV3:
    def test_new_defaults(self, db):
        s = db.get_settings(CHAT)
        assert s.min_buy_usdt == pytest.approx(5.0)
        assert s.whale_usdt == pytest.approx(500.0)
        assert s.emoji_step_usdt == pytest.approx(25.0)

    def test_roundtrip_usdt_fields(self, db):
        s = GroupSettings(
            chat_id=CHAT, min_buy_usdt=25.0, whale_usdt=1000.0, emoji_step_usdt=50.0
        )
        db.save_settings(s)
        loaded = db.get_settings(CHAT)
        assert loaded == s
        assert loaded.min_buy_usdt == pytest.approx(25.0)
        assert loaded.whale_usdt == pytest.approx(1000.0)
        assert loaded.emoji_step_usdt == pytest.approx(50.0)

    def test_mon_fields_still_persisted(self, db):
        """The legacy *_mon columns stay in the schema and keep working."""
        s = GroupSettings(chat_id=CHAT, min_buy_mon=3.0, whale_mon=250.0)
        db.save_settings(s)
        loaded = db.get_settings(CHAT)
        assert loaded.min_buy_mon == pytest.approx(3.0)
        assert loaded.whale_mon == pytest.approx(250.0)


# --------------------------------------------------------- USD price alerts
class TestPriceAlertsV3:
    def test_add_usd_alert(self, db):
        alert_id = db.add_price_alert(
            CHAT, TOKEN_A, "below", 0.0, 42, target_usd=0.02, currency="USD"
        )
        alert = db.list_price_alerts(CHAT)[0]
        assert isinstance(alert, PriceAlert)
        assert alert.id == alert_id
        assert alert.currency == "USD"
        assert alert.target_usd == pytest.approx(0.02)
        assert alert.target_mon == pytest.approx(0.0)

    def test_add_mon_alert_still_works(self, db):
        """v2-style call (no new kwargs) stays MON with target_usd None."""
        db.add_price_alert(CHAT, TOKEN_A, "above", 0.5, 42)
        alert = db.list_price_alerts(CHAT)[0]
        assert alert.currency == "MON"
        assert alert.target_usd is None
        assert alert.target_mon == pytest.approx(0.5)

    def test_all_active_includes_currency(self, db):
        db.add_price_alert(CHAT, TOKEN_A, "above", 0.0, 42, target_usd=1.0, currency="USD")
        alerts = db.all_active_price_alerts()
        assert alerts[0].currency == "USD"
        assert alerts[0].target_usd == pytest.approx(1.0)


# ------------------------------------------------------------- migration
class TestMigrationFromV2:
    def test_open_v2_db_migrates_settings(self, tmp_path):
        path = str(tmp_path / "v2.db")
        _make_v2_db(path)
        db = Database(path)  # must never raise
        try:
            s = db.get_settings(CHAT)
            # v2 data preserved
            assert s.language == "es"
            assert s.min_buy_mon == pytest.approx(7.0)
            assert s.sell_alerts is True
            # new v3 columns with defaults
            assert s.min_buy_usdt == pytest.approx(5.0)
            assert s.whale_usdt == pytest.approx(500.0)
            assert s.emoji_step_usdt == pytest.approx(25.0)
        finally:
            db.close()

    def test_v2_alerts_become_mon_currency(self, tmp_path):
        path = str(tmp_path / "v2.db")
        _make_v2_db(path)
        db = Database(path)
        try:
            alerts = db.list_price_alerts(CHAT)
            assert len(alerts) == 1
            alert = alerts[0]
            assert alert.currency == "MON"  # legacy alerts keep MON semantics
            assert alert.target_usd is None
            assert alert.target_mon == pytest.approx(0.5)
            assert alert.direction == "above"
        finally:
            db.close()

    def test_v2_db_can_save_usdt_fields(self, tmp_path):
        path = str(tmp_path / "v2.db")
        _make_v2_db(path)
        db = Database(path)
        s = db.get_settings(CHAT)
        s.min_buy_usdt = 42.0
        db.save_settings(s)
        db.close()
        db2 = Database(path)
        try:
            assert db2.get_settings(CHAT).min_buy_usdt == pytest.approx(42.0)
        finally:
            db2.close()

    def test_migration_is_idempotent(self, tmp_path):
        path = str(tmp_path / "v2.db")
        _make_v2_db(path)
        for _ in range(3):
            db = Database(path)  # opening repeatedly must never fail
            db.close()
