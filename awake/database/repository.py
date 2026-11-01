"""Data access for sessions and settings. All queries are parameterised."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Literal

from awake.database.database import Database
from awake.database.models import EndReason, Session, SessionStatus, utcnow
from awake.logging_setup import kv

log = logging.getLogger("awake.database")

SortKey = Literal["started_at", "ended_at", "duration_seconds", "status"]
_SORT_COLUMNS: dict[str, str] = {
    "started_at": "started_at",
    "ended_at": "ended_at",
    "duration_seconds": "duration_seconds",
    "status": "status",
}

# Ended sessions older than this are pruned on startup.
HISTORY_RETENTION = timedelta(days=365)


def _to_iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _from_iso(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _row_to_session(row: sqlite3.Row) -> Session:
    return Session(
        id=row["id"],
        started_at=datetime.fromisoformat(row["started_at"]),
        duration_seconds=row["duration_seconds"],
        system_awake=bool(row["system_awake"]),
        display_awake=bool(row["display_awake"]),
        status=SessionStatus(row["status"]),
        ended_at=_from_iso(row["ended_at"]),
        end_reason=EndReason(row["end_reason"]) if row["end_reason"] else None,
    )


class SessionRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def insert(self, session: Session) -> None:
        self._db.execute(
            """
            INSERT INTO sessions (id, started_at, ended_at, duration_seconds,
                                  system_awake, display_awake, status, end_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session.id,
                _to_iso(session.started_at),
                _to_iso(session.ended_at),
                session.duration_seconds,
                int(session.system_awake),
                int(session.display_awake),
                session.status.value,
                session.end_reason.value if session.end_reason else None,
            ),
        )

    def update(self, session: Session) -> None:
        self._db.execute(
            """
            UPDATE sessions
               SET ended_at = ?, duration_seconds = ?, system_awake = ?,
                   display_awake = ?, status = ?, end_reason = ?
             WHERE id = ?
            """,
            (
                _to_iso(session.ended_at),
                session.duration_seconds,
                int(session.system_awake),
                int(session.display_awake),
                session.status.value,
                session.end_reason.value if session.end_reason else None,
                session.id,
            ),
        )

    def get(self, session_id: str) -> Session | None:
        row = self._db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return _row_to_session(row) if row else None

    def recent(self, limit: int = 3, *, include_active: bool = False) -> list[Session]:
        where = "" if include_active else "WHERE status != 'active'"
        rows = self._db.execute(
            f"SELECT * FROM sessions {where} ORDER BY started_at DESC LIMIT ?", (int(limit),)
        ).fetchall()
        return [_row_to_session(r) for r in rows]

    def all(self, sort: SortKey = "started_at", descending: bool = True) -> list[Session]:
        column = _SORT_COLUMNS.get(sort)
        if column is None:
            raise ValueError(f"Unsupported sort key: {sort!r}")
        direction = "DESC" if descending else "ASC"
        # Column/direction come from a fixed whitelist above, never from user input.
        rows = self._db.execute(
            f"SELECT * FROM sessions ORDER BY {column} {direction}, started_at DESC"
        ).fetchall()
        return [_row_to_session(r) for r in rows]

    def count(self) -> int:
        return int(self._db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0])

    def clear(self) -> int:
        """Delete all finished sessions. An active session is kept."""
        cursor = self._db.execute("DELETE FROM sessions WHERE status != 'active'")
        log.info(kv("history_cleared", deleted=cursor.rowcount))
        return cursor.rowcount

    def recover_stale(self) -> int:
        """Mark sessions left 'active' by a crash or forced termination as interrupted.

        No heartbeat is written while a session runs (to keep disk writes minimal),
        so the true end time of an interrupted session is unknown and stays NULL.
        """
        cursor = self._db.execute(
            "UPDATE sessions SET status = ?, end_reason = ? WHERE status = 'active'",
            (SessionStatus.INTERRUPTED.value, EndReason.APP_TERMINATED.value),
        )
        if cursor.rowcount:
            log.warning(kv("stale_sessions_recovered", count=cursor.rowcount))
        return cursor.rowcount

    def prune(self, now: datetime | None = None, retention: timedelta = HISTORY_RETENTION) -> int:
        cutoff = (now or utcnow()) - retention
        cursor = self._db.execute(
            "DELETE FROM sessions WHERE status != 'active' AND started_at < ?",
            (_to_iso(cutoff),),
        )
        if cursor.rowcount:
            log.info(kv("history_pruned", deleted=cursor.rowcount))
        return cursor.rowcount


class SettingsRepository:
    """Raw key/value storage. Values are JSON strings encoded by SettingsManager."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def load_all(self) -> dict[str, str]:
        rows = self._db.execute("SELECT key, value FROM settings").fetchall()
        return {row["key"]: row["value"] for row in rows}

    def save(self, key: str, value: str) -> None:
        self._db.execute(
            """
            INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, value, _to_iso(utcnow())),
        )

    def delete(self, key: str) -> None:
        self._db.execute("DELETE FROM settings WHERE key = ?", (key,))
