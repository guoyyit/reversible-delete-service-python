"""SQLite storage layer: schema, connection handling, transactions."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS resources (
    id TEXT PRIMARY KEY,
    parent_id TEXT REFERENCES resources(id),
    name TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    content TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT,
    delete_root_id TEXT,
    deleted_by TEXT,
    delete_reason TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_resources_alive_name
    ON resources(COALESCE(parent_id, ''), name)
    WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_resources_parent ON resources(parent_id);
CREATE INDEX IF NOT EXISTS ix_resources_deleted_at ON resources(deleted_at);
CREATE INDEX IF NOT EXISTS ix_resources_delete_root ON resources(delete_root_id);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    resource_id TEXT,
    detail TEXT
);
"""


class Database:
    """Single-connection SQLite store guarded by an RLock.

    All multi-row state transitions run inside one transaction, so any
    concurrent reader observes each resource in exactly one state
    (deleted or not deleted) -- never a field-level mix.
    """

    def __init__(self, path: str = ":memory:"):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._lock = threading.RLock()
        with self._conn:
            self._conn.executescript(SCHEMA)

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Exclusive, atomic unit of work. Rolls back on any exception."""
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                yield self._conn
            except Exception:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
