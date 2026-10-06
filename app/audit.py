"""Audit trail helpers (written inside the same transaction as the action)."""

from __future__ import annotations

import sqlite3

ACTION_DELETE = "delete"
ACTION_RESTORE = "restore"
ACTION_PURGE = "purge"
ACTION_DELETE_DENIED = "delete_denied"
ACTION_RESTORE_DENIED = "restore_denied"
ACTION_RESTORE_CONFLICT = "restore_conflict"


def record(
    conn: sqlite3.Connection,
    *,
    ts: str,
    actor: str,
    action: str,
    resource_id: str | None,
    detail: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO audit_log (ts, actor, action, resource_id, detail)"
        " VALUES (?, ?, ?, ?, ?)",
        (ts, actor, action, resource_id, detail),
    )
