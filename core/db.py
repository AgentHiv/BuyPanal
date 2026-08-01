"""SQLite persistence for the Monad buy bot (SPEC 5.3)."""

from __future__ import annotations

import os
import sqlite3
import time
from typing import TYPE_CHECKING

from core.models import GroupSettings

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
"""


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
        return GroupSettings(
            chat_id=row["chat_id"],
            language=row["language"],
            buy_emoji=row["buy_emoji"],
            whale_emoji=row["whale_emoji"],
            min_buy_mon=row["min_buy_mon"],
            whale_mon=row["whale_mon"],
            emoji_step_mon=row["emoji_step_mon"],
        )

    def save_settings(self, settings: GroupSettings) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO group_settings (
                    chat_id, language, buy_emoji, whale_emoji,
                    min_buy_mon, whale_mon, emoji_step_mon
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    language       = excluded.language,
                    buy_emoji      = excluded.buy_emoji,
                    whale_emoji    = excluded.whale_emoji,
                    min_buy_mon    = excluded.min_buy_mon,
                    whale_mon      = excluded.whale_mon,
                    emoji_step_mon = excluded.emoji_step_mon
                """,
                (
                    settings.chat_id,
                    settings.language,
                    settings.buy_emoji,
                    settings.whale_emoji,
                    settings.min_buy_mon,
                    settings.whale_mon,
                    settings.emoji_step_mon,
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
