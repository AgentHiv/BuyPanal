"""SQLite persistence for the Monad buy bot (SPEC 5.3 + SPEC-v2 §2).

v2 additions: price alerts, new GroupSettings fields (sell_alerts,
scanner_alerts, sell_emoji) and ``list_known_chats()``. Opening a v1
database transparently migrates it via guarded ``ALTER TABLE`` statements
(PRAGMA table_info) — it never fails.
"""

from __future__ import annotations

import os
import sqlite3
import time
from typing import TYPE_CHECKING

from core.models import GroupSettings, PriceAlert

if TYPE_CHECKING:  # avoid a hard import cycle; BuyEvent is only typed
    from core.models import BuyEvent

_SECONDS_24H = 24 * 60 * 60

_SCHEMA = """
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

CREATE INDEX IF NOT EXISTS idx_buys_chat_ts ON buys (chat_id, ts);
CREATE INDEX IF NOT EXISTS idx_buys_token ON buys (token_address);

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

CREATE INDEX IF NOT EXISTS idx_price_alerts_chat ON price_alerts (chat_id, active);
"""

# Columns added in v2 to group_settings: (name, DDL fragment). New
# databases get them via _migrate() too (kept out of _SCHEMA so the v1
# CREATE TABLE stays byte-identical and the migration path is exercised
# on every open).
_SETTINGS_V2_COLUMNS = (
    ("sell_alerts", "sell_alerts INTEGER NOT NULL DEFAULT 0"),
    ("scanner_alerts", "scanner_alerts INTEGER NOT NULL DEFAULT 0"),
    ("sell_emoji", "sell_emoji TEXT NOT NULL DEFAULT '🔴'"),
)

# Columns added in v3 (SPEC-v3 §2): USDT thresholds for group_settings.
# The legacy *_mon columns stay in the schema untouched.
_SETTINGS_V3_COLUMNS = (
    ("min_buy_usdt", "min_buy_usdt REAL NOT NULL DEFAULT 5.0"),
    ("whale_usdt", "whale_usdt REAL NOT NULL DEFAULT 500.0"),
    ("emoji_step_usdt", "emoji_step_usdt REAL NOT NULL DEFAULT 25.0"),
)

# Columns added in v3 to price_alerts: USD-denominated alerts. Existing v2
# rows get currency='MON' via the column default.
_ALERTS_V3_COLUMNS = (
    ("target_usd", "target_usd REAL"),
    ("currency", "currency TEXT NOT NULL DEFAULT 'MON'"),
)


def _norm(address: str) -> str:
    """Normalize an address for case-insensitive storage/lookup."""
    return address.strip().lower()


class Database:
    def __init__(self, path: str) -> None:
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.path = path
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.executescript(_SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Add v2/v3 columns to older tables. Never fails."""
        self._migrate_table("group_settings", _SETTINGS_V2_COLUMNS + _SETTINGS_V3_COLUMNS)
        self._migrate_table("price_alerts", _ALERTS_V3_COLUMNS)

    def _migrate_table(self, table: str, columns) -> None:
        """Guarded ``ALTER TABLE ADD COLUMN`` for any missing columns."""
        try:
            rows = self._conn.execute(f"PRAGMA table_info({table})").fetchall()
            existing = {row["name"] for row in rows}
            with self._conn:
                for name, ddl in columns:
                    if name in existing:
                        continue
                    try:
                        self._conn.execute(
                            f"ALTER TABLE {table} ADD COLUMN {ddl}"
                        )
                    except sqlite3.Error:
                        # Another process raced us, etc. — column likely there.
                        pass
        except sqlite3.Error:
            # A broken/legacy database must still open; degrade gracefully.
            pass

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------
    # groups
    # ------------------------------------------------------------------
    def get_settings(self, chat_id: int) -> GroupSettings:
        """Return the group's settings, or defaults if never saved."""
        row = self._conn.execute(
            "SELECT * FROM group_settings WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        if row is None:
            return GroupSettings(chat_id=chat_id)
        keys = set(row.keys())  # tolerate unmigrated/legacy tables
        return GroupSettings(
            chat_id=row["chat_id"],
            language=row["language"],
            buy_emoji=row["buy_emoji"],
            whale_emoji=row["whale_emoji"],
            min_buy_mon=row["min_buy_mon"],
            whale_mon=row["whale_mon"],
            emoji_step_mon=row["emoji_step_mon"],
            sell_alerts=bool(row["sell_alerts"]) if "sell_alerts" in keys else False,
            scanner_alerts=(
                bool(row["scanner_alerts"]) if "scanner_alerts" in keys else False
            ),
            sell_emoji=row["sell_emoji"] if "sell_emoji" in keys else "🔴",
            min_buy_usdt=float(row["min_buy_usdt"]) if "min_buy_usdt" in keys else 5.0,
            whale_usdt=float(row["whale_usdt"]) if "whale_usdt" in keys else 500.0,
            emoji_step_usdt=(
                float(row["emoji_step_usdt"]) if "emoji_step_usdt" in keys else 25.0
            ),
        )

    def save_settings(self, settings: GroupSettings) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO group_settings (
                    chat_id, language, buy_emoji, whale_emoji,
                    min_buy_mon, whale_mon, emoji_step_mon,
                    sell_alerts, scanner_alerts, sell_emoji,
                    min_buy_usdt, whale_usdt, emoji_step_usdt
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    language       = excluded.language,
                    buy_emoji      = excluded.buy_emoji,
                    whale_emoji    = excluded.whale_emoji,
                    min_buy_mon    = excluded.min_buy_mon,
                    whale_mon      = excluded.whale_mon,
                    emoji_step_mon = excluded.emoji_step_mon,
                    sell_alerts    = excluded.sell_alerts,
                    scanner_alerts = excluded.scanner_alerts,
                    sell_emoji     = excluded.sell_emoji,
                    min_buy_usdt   = excluded.min_buy_usdt,
                    whale_usdt     = excluded.whale_usdt,
                    emoji_step_usdt = excluded.emoji_step_usdt
                """,
                (
                    settings.chat_id,
                    settings.language,
                    settings.buy_emoji,
                    settings.whale_emoji,
                    settings.min_buy_mon,
                    settings.whale_mon,
                    settings.emoji_step_mon,
                    int(settings.sell_alerts),
                    int(settings.scanner_alerts),
                    settings.sell_emoji,
                    float(settings.min_buy_usdt),
                    float(settings.whale_usdt),
                    float(settings.emoji_step_usdt),
                ),
            )

    # ------------------------------------------------------------------
    # tracked tokens
    # ------------------------------------------------------------------
    def add_token(self, chat_id: int, address: str, kind: str = "unknown") -> bool:
        """Track ``address`` for ``chat_id``. False if already tracked."""
        address = _norm(address)
        with self._conn:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO tracked_tokens (chat_id, address, kind)"
                " VALUES (?, ?, ?)",
                (chat_id, address, kind),
            )
        return cur.rowcount > 0

    def remove_token(self, chat_id: int, address: str) -> bool:
        """Stop tracking ``address`` for ``chat_id``. False if not tracked."""
        address = _norm(address)
        with self._conn:
            cur = self._conn.execute(
                "DELETE FROM tracked_tokens WHERE chat_id = ? AND address = ?",
                (chat_id, address),
            )
        return cur.rowcount > 0

    def list_tokens(self, chat_id: int) -> list[str]:
        rows = self._conn.execute(
            "SELECT address FROM tracked_tokens WHERE chat_id = ? ORDER BY address",
            (chat_id,),
        ).fetchall()
        return [row["address"] for row in rows]

    def all_tracked_tokens(self) -> dict[str, list[int]]:
        """Map every tracked token address to the chat_ids tracking it."""
        rows = self._conn.execute(
            "SELECT address, chat_id FROM tracked_tokens ORDER BY address, chat_id"
        ).fetchall()
        result: dict[str, list[int]] = {}
        for row in rows:
            result.setdefault(row["address"], []).append(row["chat_id"])
        return result

    # ------------------------------------------------------------------
    # stats
    # ------------------------------------------------------------------
    def record_buy(self, chat_id: int, buy: "BuyEvent") -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO buys (
                    chat_id, token_address, buyer,
                    amount_mon, amount_usd, tx_hash, ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chat_id,
                    _norm(buy.token_address),
                    buy.buyer,
                    float(buy.amount_mon),
                    None if buy.amount_usd is None else float(buy.amount_usd),
                    buy.tx_hash,
                    int(buy.timestamp),
                ),
            )

    def has_bought_before(self, chat_id: int, token_address: str, buyer: str) -> bool:
        """True if this buyer already bought this token in this group.

        Buyer addresses are stored un-normalized, so compare case-insensitively.
        """
        row = self._conn.execute(
            "SELECT 1 FROM buys"
            " WHERE chat_id = ? AND token_address = ? AND LOWER(buyer) = LOWER(?)"
            " LIMIT 1",
            (chat_id, _norm(token_address), buyer),
        ).fetchone()
        return row is not None

    def get_stats_24h(self, chat_id: int, token: str | None = None) -> dict:
        """24h aggregates -> {"count": int, "volume_mon": float, "volume_usd": float}."""
        since = int(time.time()) - _SECONDS_24H
        sql = (
            "SELECT COUNT(*) AS count,"
            "       COALESCE(SUM(amount_mon), 0.0) AS volume_mon,"
            "       COALESCE(SUM(amount_usd), 0.0) AS volume_usd"
            "  FROM buys"
            " WHERE chat_id = ? AND ts >= ?"
        )
        params: list = [chat_id, since]
        if token is not None:
            sql += " AND token_address = ?"
            params.append(_norm(token))
        row = self._conn.execute(sql, params).fetchone()
        return {
            "count": int(row["count"]),
            "volume_mon": float(row["volume_mon"]),
            "volume_usd": float(row["volume_usd"]),
        }

    def get_top_buyers(
        self, chat_id: int, token: str | None = None, limit: int = 10
    ) -> list[tuple[str, float]]:
        """Top buyers by total MON spent -> [(buyer, total_mon), ...] desc."""
        sql = (
            "SELECT buyer, SUM(amount_mon) AS total_mon"
            "  FROM buys"
            " WHERE chat_id = ?"
        )
        params: list = [chat_id]
        if token is not None:
            sql += " AND token_address = ?"
            params.append(_norm(token))
        sql += " GROUP BY buyer ORDER BY total_mon DESC LIMIT ?"
        params.append(int(limit))
        rows = self._conn.execute(sql, params).fetchall()
        return [(row["buyer"], float(row["total_mon"])) for row in rows]

    # ------------------------------------------------------------------
    # price alerts (SPEC-v2 §2)
    # ------------------------------------------------------------------
    @staticmethod
    def _row_to_alert(row: sqlite3.Row) -> PriceAlert:
        keys = set(row.keys())  # tolerate unmigrated/legacy tables
        target_usd = row["target_usd"] if "target_usd" in keys else None
        return PriceAlert(
            id=int(row["id"]),
            chat_id=int(row["chat_id"]),
            token_address=row["token_address"],
            direction=row["direction"],
            target_mon=float(row["target_mon"]),
            created_by=int(row["created_by"]),
            active=bool(row["active"]),
            target_usd=None if target_usd is None else float(target_usd),
            currency=row["currency"] if "currency" in keys else "MON",
        )

    def add_price_alert(
        self,
        chat_id: int,
        address: str,
        direction: str,
        target_mon: float,
        created_by: int,
        target_usd: "float | None" = None,
        currency: str = "MON",
    ) -> int:
        """Insert a price alert and return its id.

        SPEC-v3: USD alerts pass ``target_usd`` + ``currency="USD"``; v2 MON
        alerts keep working with the original positional arguments.
        """
        with self._conn:
            cur = self._conn.execute(
                """
                INSERT INTO price_alerts (
                    chat_id, token_address, direction, target_mon,
                    created_by, active, created_at, target_usd, currency
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    chat_id,
                    _norm(address),
                    str(direction),
                    float(target_mon),
                    int(created_by),
                    int(time.time()),
                    None if target_usd is None else float(target_usd),
                    str(currency or "MON"),
                ),
            )
        return int(cur.lastrowid)

    def list_price_alerts(
        self, chat_id: int, active_only: bool = True
    ) -> list[PriceAlert]:
        sql = "SELECT * FROM price_alerts WHERE chat_id = ?"
        params: list = [chat_id]
        if active_only:
            sql += " AND active = 1"
        sql += " ORDER BY id"
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_alert(row) for row in rows]

    def deactivate_price_alert(self, alert_id: int, chat_id: int) -> bool:
        """Mark alert inactive (scoped to its chat). False if not found."""
        with self._conn:
            cur = self._conn.execute(
                "UPDATE price_alerts SET active = 0"
                " WHERE id = ? AND chat_id = ? AND active = 1",
                (int(alert_id), int(chat_id)),
            )
        return cur.rowcount > 0

    def all_active_price_alerts(self) -> list[PriceAlert]:
        """Every active alert across all chats (used by PriceMonitor)."""
        rows = self._conn.execute(
            "SELECT * FROM price_alerts WHERE active = 1 ORDER BY id"
        ).fetchall()
        return [self._row_to_alert(row) for row in rows]

    # ------------------------------------------------------------------
    # misc (SPEC-v2 §8.3)
    # ------------------------------------------------------------------
    def list_known_chats(self) -> list[int]:
        """Chats with saved settings OR tracked tokens (DISTINCT union)."""
        rows = self._conn.execute(
            """
            SELECT chat_id FROM group_settings
            UNION
            SELECT chat_id FROM tracked_tokens
            ORDER BY chat_id
            """
        ).fetchall()
        return [int(row["chat_id"]) for row in rows]
