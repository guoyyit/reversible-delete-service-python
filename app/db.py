"""SQLite storage layer: schema, connection handling, transactions."""

from __future__ import annotations

import sqlite3
import threading

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

    All state transitions happen inside transactions so a resource is
    always observed in exactly one state (deleted or not).
    """

    def __init__(self, path: str = ":memory:"):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._lock = threading.RLock()
        with self._conn:
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()
