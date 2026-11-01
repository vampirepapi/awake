"""SQLite connection management and schema migrations.

The schema version is tracked with ``PRAGMA user_version``. Each entry in
``MIGRATIONS`` upgrades the schema by exactly one version and runs inside a
transaction, so a failed migration leaves the database untouched.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from awake.logging_setup import kv

log = logging.getLogger("awake.database")


class DatabaseError(RuntimeError):
    """Raised when the database cannot be opened, migrated or written."""


MIGRATIONS: list[str] = [
    # v1 — initial schema
    """
    CREATE TABLE sessions (
        id                TEXT PRIMARY KEY,
        started_at        TEXT NOT NULL,
        ended_at          TEXT,
        duration_seconds  INTEGER,
        system_awake      INTEGER NOT NULL DEFAULT 1,
        display_awake     INTEGER NOT NULL DEFAULT 0,
        status            TEXT NOT NULL,
        end_reason        TEXT
    );
    CREATE INDEX idx_sessions_started_at ON sessions (started_at DESC);
    CREATE INDEX idx_sessions_status ON sessions (status);

    CREATE TABLE settings (
        key         TEXT PRIMARY KEY,
        value       TEXT NOT NULL,
        updated_at  TEXT NOT NULL
    );
    """,
]

SCHEMA_VERSION = len(MIGRATIONS)


class Database:
    """Owns the single SQLite connection used by the GUI thread."""

    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        try:
            self._conn = sqlite3.connect(self.path, isolation_level=None)  # autocommit; explicit txns
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA busy_timeout = 2000")
        except sqlite3.Error as exc:
            log.error(kv("database_open_failed", path=self.path, error=exc))
            raise DatabaseError(f"Could not open the Awake database: {exc}") from exc
        self._migrate()

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    @property
    def schema_version(self) -> int:
        return int(self._conn.execute("PRAGMA user_version").fetchone()[0])

    def _migrate(self) -> None:
        current = self.schema_version
        if current > SCHEMA_VERSION:
            raise DatabaseError(
                f"Database schema v{current} is newer than this version of Awake supports "
                f"(v{SCHEMA_VERSION}). Please update Awake."
            )
        for version in range(current, SCHEMA_VERSION):
            script = MIGRATIONS[version]
            try:
                # executescript() commits implicitly, so wrap the script in its own transaction.
                self._conn.executescript(
                    f"BEGIN;\n{script}\nPRAGMA user_version = {version + 1};\nCOMMIT;"
                )
            except sqlite3.Error as exc:
                if self._conn.in_transaction:
                    self._conn.rollback()
                log.error(kv("database_migration_failed", target_version=version + 1, error=exc))
                raise DatabaseError(f"Database migration to v{version + 1} failed: {exc}") from exc
            log.info(kv("database_migrated", version=version + 1))

    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        try:
            return self._conn.execute(sql, params)
        except sqlite3.Error as exc:
            log.error(kv("database_query_failed", error=exc))
            raise DatabaseError(str(exc)) from exc

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error as exc:  # pragma: no cover - defensive
            log.warning(kv("database_close_failed", error=exc))
