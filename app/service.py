"""Resource service: reversible delete, restore, retention recycling.

Design notes
------------
* Soft delete sets ``deleted_at`` (+ delete metadata) in ONE UPDATE per row,
  and the whole cascade in ONE transaction: readers only ever observe a
  single, consistent state per resource.
* Uniqueness of (parent_id, name) is enforced by a partial unique index that
  only covers non-deleted rows. Deleting frees the name; restoring into a
  name that was re-created fails with a deterministic conflict.
* Deleting a parent cascades to all currently-alive descendants and tags
  them with ``delete_root_id``. Restoring the root restores exactly that
  set, so no orphans and no duplicate associations are produced.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

from . import audit
from .auth import Caller
from .config import Settings
from .db import Database
from .errors import ConflictError, ForbiddenError, NotFoundError, ValidationError
from .models import AuditEntry, Resource

_SELECT = (
    "SELECT id, name, parent_id, owner_id, content, created_at, updated_at,"
    " deleted_at, delete_root_id, deleted_by, delete_reason FROM resources"
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _row_to_resource(row: sqlite3.Row) -> Resource:
    return Resource(
        id=row["id"],
        name=row["name"],
        parent_id=row["parent_id"],
        owner_id=row["owner_id"],
        content=row["content"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        deleted_at=row["deleted_at"],
        delete_root_id=row["delete_root_id"],
        deleted_by=row["deleted_by"],
        delete_reason=row["delete_reason"],
    )


class BatchConflictError(ConflictError):
    """Atomic batch failed as a whole; carries per-item error payload."""

    code = "batch_failed"

    def __init__(self, payload: dict):
        super().__init__("atomic batch failed, nothing was applied")
        self.payload = payload


class ResourceService:
    def __init__(self, db: Database, settings: Settings):
        self._db = db
        self._settings = settings

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------
    def _get_row(self, conn: sqlite3.Connection, resource_id: str) -> sqlite3.Row:
        row = conn.execute(
            _SELECT + " WHERE id = ?", (resource_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError(f"resource {resource_id!r} not found")
        return row

    def _alive_descendants(
        self, conn: sqlite3.Connection, root_id: str
    ) -> list[sqlite3.Row]:
        """All currently-alive descendants of root_id (BFS)."""
        result: list[sqlite3.Row] = []
        frontier = [root_id]
        while frontier:
            rows = conn.execute(
                _SELECT
                + f" WHERE deleted_at IS NULL AND parent_id IN"
                  f" ({','.join('?' * len(frontier))})",
                tuple(frontier),
            ).fetchall()
            result.extend(rows)
            frontier = [r["id"] for r in rows]
        return result

    def _has_deleted_ancestor(
        self, conn: sqlite3.Connection, row: sqlite3.Row
    ) -> bool:
        seen = {row["id"]}
        parent_id = row["parent_id"]
        while parent_id is not None and parent_id not in seen:
            seen.add(parent_id)
            parent = conn.execute(
                "SELECT parent_id, deleted_at FROM resources WHERE id = ?",
                (parent_id,),
            ).fetchone()
            if parent is None:
                return False
            if parent["deleted_at"] is not None:
                return True
            parent_id = parent["parent_id"]
        return False

    def _check_permission(
        self, caller: Caller, action: str, owner_id: str, resource_id: str
    ) -> None:
        """Raise (and persist a denied-audit record) if not owner/admin."""
        try:
            caller.require_owner_or_admin(owner_id)
        except ForbiddenError as exc:
            denied_action = (
                audit.ACTION_DELETE_DENIED
                if action == audit.ACTION_DELETE
                else audit.ACTION_RESTORE_DENIED
            )
            with self._db.transaction() as conn:
                audit.record(
                    conn,
                    ts=_iso(_utcnow()),
                    actor=caller.user_id,
                    action=denied_action,
                    resource_id=resource_id,
                    detail=str(exc),
                )
            raise

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    def create(
        self,
        caller: Caller,
        *,
        name: str,
        parent_id: str | None = None,
        content: str | None = None,
        owner_id: str | None = None,
    ) -> Resource:
        if not name or not name.strip():
            raise ValidationError("name must not be empty")
        owner = owner_id or caller.user_id
        if owner != caller.user_id and not caller.is_admin:
            raise ForbiddenError("only admins may create resources for others")
        now = _iso(_utcnow())
        resource_id = uuid.uuid4().hex
        with self._db.transaction() as conn:
            if parent_id is not None:
                parent = self._get_row(conn, parent_id)
                if parent["deleted_at"] is not None:
                    raise NotFoundError(
                        f"parent {parent_id!r} not found"
                    )
            try:
                conn.execute(
                    "INSERT INTO resources"
                    " (id, name, parent_id, owner_id, content, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (resource_id, name, parent_id, owner, content, now, now),
                )
            except sqlite3.IntegrityError:
                raise ConflictError(
                    f"resource named {name!r} already exists under this parent",
                    code="name_conflict",
                ) from None
            return _row_to_resource(self._get_row(conn, resource_id))

    def get(self, resource_id: str, *, include_deleted: bool = False) -> Resource:
        with self._db.lock:
            row = self._db.query_one(_SELECT + " WHERE id = ?", (resource_id,))
            if row is None:
                raise NotFoundError(f"resource {resource_id!r} not found")
            if not include_deleted and row["deleted_at"] is not None:
                raise NotFoundError(f"resource {resource_id!r} not found")
            return _row_to_resource(row)

    def list(
        self,
        *,
        parent_id: str | None = None,
        include_deleted: bool = False,
    ) -> list[Resource]:
        sql = _SELECT
        conds: list[str] = []
        params: list[str] = []
        if parent_id is not None:
            conds.append("parent_id = ?")
            params.append(parent_id)
        if not include_deleted:
            conds.append("deleted_at IS NULL")
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY created_at, id"
        return [_row_to_resource(r) for r in self._db.query(sql, tuple(params))]

    def children(
        self, parent_id: str, *, include_deleted: bool = False
    ) -> list[Resource]:
        parent = self.get(parent_id, include_deleted=include_deleted)
        if parent.deleted_at is not None and not include_deleted:
            raise NotFoundError(f"resource {parent_id!r} not found")
        return self.list(parent_id=parent_id, include_deleted=include_deleted)

    def count(self, *, include_deleted: bool = False) -> int:
        sql = "SELECT COUNT(*) AS c FROM resources"
        if not include_deleted:
            sql += " WHERE deleted_at IS NULL"
        return self._db.query_one(sql)["c"]

    # ------------------------------------------------------------------
    # reversible delete
    # ------------------------------------------------------------------
    def delete(
        self, caller: Caller, resource_id: str, *, reason: str | None = None
    ) -> Resource:
        row = self._db.query_one(_SELECT + " WHERE id = ?", (resource_id,))
        if row is None:
            raise NotFoundError(f"resource {resource_id!r} not found")
        self._check_permission(caller, audit.ACTION_DELETE, row["owner_id"], resource_id)
        if row["deleted_at"] is not None:
            return _row_to_resource(row)  # idempotent: already deleted

        with self._db.transaction() as conn:
            self._delete_in_tx(conn, caller, resource_id, reason=reason)
            return _row_to_resource(self._get_row(conn, resource_id))

    def _delete_in_tx(
        self,
        conn: sqlite3.Connection,
        caller: Caller,
        resource_id: str,
        *,
        reason: str | None,
    ) -> None:
        """Soft-delete one resource (cascading) inside an open transaction."""
        now = _iso(_utcnow())
        descendants = self._alive_descendants(conn, resource_id)
        cascade_ids = [resource_id] + [r["id"] for r in descendants]
        conn.execute(
            f"UPDATE resources SET deleted_at = ?, delete_root_id = ?,"
            f" deleted_by = ?, delete_reason = ?, updated_at = ?"
            f" WHERE deleted_at IS NULL AND id IN"
            f" ({','.join('?' * len(cascade_ids))})",
            (now, resource_id, caller.user_id, reason, now, *cascade_ids),
        )
        audit.record(
            conn,
            ts=now,
            actor=caller.user_id,
            action=audit.ACTION_DELETE,
            resource_id=resource_id,
            detail=f"cascade_size={len(cascade_ids)} reason={reason!r}",
        )

    def restore(self, caller: Caller, resource_id: str) -> Resource:
        row = self._db.query_one(_SELECT + " WHERE id = ?", (resource_id,))
        if row is None:
            raise NotFoundError(f"resource {resource_id!r} not found")
        self._check_permission(caller, audit.ACTION_RESTORE, row["owner_id"], resource_id)
        if row["deleted_at"] is None:
            return _row_to_resource(row)  # idempotent: not deleted

        now_dt = _utcnow()
        now = _iso(now_dt)
        deleted_at = _parse(row["deleted_at"])
        if deleted_at + timedelta(seconds=self._settings.retention_seconds) < now_dt:
            with self._db.transaction() as conn:
                audit.record(
                    conn,
                    ts=now,
                    actor=caller.user_id,
                    action=audit.ACTION_RESTORE_DENIED,
                    resource_id=resource_id,
                    detail="retention period expired",
                )
            raise ConflictError(
                "retention period expired, resource cannot be restored",
                code="retention_expired",
            )
        if row["delete_root_id"] != resource_id:
            raise ConflictError(
                "resource was deleted as part of a cascade; "
                f"restore root {row['delete_root_id']!r} instead",
                code="cascade_member",
            )

        with self._db.transaction() as conn:
            self._restore_in_tx(conn, caller, row)
            return _row_to_resource(self._get_row(conn, resource_id))

    def _restore_in_tx(
        self, conn: sqlite3.Connection, caller: Caller, row: sqlite3.Row
    ) -> None:
        """Restore a deletion root (with its cascade set) in a transaction."""
        resource_id = row["id"]
        now = _iso(_utcnow())
        members = conn.execute(
            _SELECT + " WHERE delete_root_id = ?", (resource_id,)
        ).fetchall()
        member_ids = {m["id"] for m in members}
        # Ancestors of the root must be alive, otherwise restoring
        # would create an orphan invisible subtree.
        if self._has_deleted_ancestor(conn, row):
            raise ConflictError(
                "an ancestor of this resource is deleted; restore it first",
                code="ancestor_deleted",
            )
        # Deterministic name-conflict check for every member.
        for m in members:
            clash = conn.execute(
                "SELECT id FROM resources WHERE deleted_at IS NULL"
                " AND COALESCE(parent_id, '') = COALESCE(?, '')"
                " AND name = ? AND id NOT IN"
                f" ({','.join('?' * len(member_ids))})",
                (m["parent_id"], m["name"], *member_ids),
            ).fetchone()
            if clash is not None:
                audit.record(
                    conn,
                    ts=now,
                    actor=caller.user_id,
                    action=audit.ACTION_RESTORE_CONFLICT,
                    resource_id=resource_id,
                    detail=(
                        f"member {m['id']} name {m['name']!r} conflicts"
                        f" with live resource {clash['id']}"
                    ),
                )
                raise ConflictError(
                    f"name {m['name']!r} already exists under the same parent",
                    code="name_conflict",
                )
        conn.execute(
            f"UPDATE resources SET deleted_at = NULL, delete_root_id = NULL,"
            f" deleted_by = NULL, delete_reason = NULL, updated_at = ?"
            f" WHERE id IN ({','.join('?' * len(member_ids))})",
            (now, *member_ids),
        )
        audit.record(
            conn,
            ts=now,
            actor=caller.user_id,
            action=audit.ACTION_RESTORE,
            resource_id=resource_id,
            detail=f"restored={len(member_ids)}",
        )

    # ------------------------------------------------------------------
    # batch operations
    # ------------------------------------------------------------------
    def delete_batch(
        self,
        caller: Caller,
        ids: list[str],
        *,
        atomic: bool = True,
        reason: str | None = None,
    ) -> dict:
        return self._batch(
            caller, ids, atomic=atomic, op="delete", reason=reason
        )

    def restore_batch(
        self, caller: Caller, ids: list[str], *, atomic: bool = True
    ) -> dict:
        return self._batch(caller, ids, atomic=atomic, op="restore")

    def _batch(
        self,
        caller: Caller,
        ids: list[str],
        *,
        atomic: bool,
        op: str,
        reason: str | None = None,
    ) -> dict:
        if not atomic:
            succeeded: list[str] = []
            failed: list[dict] = []
            for rid in ids:
                try:
                    if op == "delete":
                        self.delete(caller, rid, reason=reason)
                    else:
                        self.restore(caller, rid)
                except Exception as exc:  # per-item isolation
                    failed.append(
                        {
                            "id": rid,
                            "code": getattr(exc, "code", "error"),
                            "message": str(exc),
                        }
                    )
                else:
                    succeeded.append(rid)
            return {
                "atomic": False,
                "applied": not failed,
                "succeeded": succeeded,
                "failed": failed,
            }

        # Atomic mode: validate every item first (no state changes), then
        # apply all items inside ONE transaction. Any failure rolls the
        # whole batch back, so callers can always tell "nothing applied"
        # (BatchConflictError) apart from "everything applied".
        action = audit.ACTION_DELETE if op == "delete" else audit.ACTION_RESTORE
        errors: list[dict] = []
        for rid in ids:
            try:
                row = self._db.query_one(_SELECT + " WHERE id = ?", (rid,))
                if row is None:
                    raise NotFoundError(f"resource {rid!r} not found")
                self._check_permission(caller, action, row["owner_id"], rid)
                if op == "restore" and row["deleted_at"] is not None:
                    self._validate_restorable(row)
            except Exception as exc:
                errors.append(
                    {
                        "id": rid,
                        "code": getattr(exc, "code", "error"),
                        "message": str(exc),
                    }
                )
        if errors:
            raise BatchConflictError(
                {
                    "atomic": True,
                    "applied": False,
                    "succeeded": [],
                    "failed": errors,
                }
            )
        try:
            with self._db.transaction() as conn:
                for rid in ids:
                    if op == "delete":
                        self._delete_in_tx(conn, caller, rid, reason=reason)
                    else:
                        row = conn.execute(
                            _SELECT + " WHERE id = ?", (rid,)
                        ).fetchone()
                        if row["deleted_at"] is not None:
                            self._restore_in_tx(conn, caller, row)
        except Exception as exc:
            raise BatchConflictError(
                {
                    "atomic": True,
                    "applied": False,
                    "succeeded": [],
                    "failed": [
                        {
                            "id": getattr(exc, "resource_id", None) or ids[0],
                            "code": getattr(exc, "code", "error"),
                            "message": str(exc),
                        }
                    ],
                }
            ) from exc
        return {"atomic": True, "applied": True, "succeeded": list(ids), "failed": []}

    def _validate_restorable(self, row: sqlite3.Row) -> None:
        """Pre-flight restore checks for atomic batches (no state changes)."""
        deleted_at = _parse(row["deleted_at"])
        if (
            deleted_at + timedelta(seconds=self._settings.retention_seconds)
            < _utcnow()
        ):
            raise ConflictError("retention period expired", code="retention_expired")
        if row["delete_root_id"] != row["id"]:
            raise ConflictError(
                "resource was deleted as part of a cascade",
                code="cascade_member",
            )

    # ------------------------------------------------------------------
    # retention recycling
    # ------------------------------------------------------------------
    def recycle_expired(
        self, caller: Caller, *, now: str | None = None
    ) -> dict:
        """Permanently purge resources whose retention has expired.

        Idempotent and re-entrant: each pass re-selects expired rows, and
        each batch commits independently, so an interrupted run leaves no
        half-deleted state and can simply be re-run.
        """
        if not caller.is_admin:
            raise ForbiddenError("only admins may run recycling")
        now_dt = _parse(now) if now else _utcnow()
        cutoff = _iso(now_dt - timedelta(seconds=self._settings.retention_seconds))

        purged: list[str] = []
        while True:
            with self._db.transaction() as conn:
                rows = conn.execute(
                    _SELECT
                    + " WHERE deleted_at IS NOT NULL AND deleted_at <= ?"
                      " ORDER BY deleted_at, id LIMIT ?",
                    (cutoff, self._settings.recycle_batch_size),
                ).fetchall()
                if not rows:
                    break
                # Expand to full deleted subtrees so no orphans remain.
                to_purge = {r["id"]: r for r in rows}
                frontier = list(to_purge)
                while frontier:
                    children = conn.execute(
                        f"SELECT id FROM resources WHERE deleted_at IS NOT NULL"
                        f" AND parent_id IN ({','.join('?' * len(frontier))})",
                        tuple(frontier),
                    ).fetchall()
                    frontier = []
                    for c in children:
                        if c["id"] not in to_purge:
                            to_purge[c["id"]] = c
                            frontier.append(c["id"])
                # Delete children before parents (topological order):
                # a row is deletable once no remaining row references it.
                remaining = set(to_purge)
                ordered: list[str] = []
                parent_of = {
                    rid: conn.execute(
                        "SELECT parent_id FROM resources WHERE id = ?", (rid,)
                    ).fetchone()["parent_id"]
                    for rid in remaining
                }
                while remaining:
                    referenced = {
                        parent_of[rid] for rid in remaining
                    } - {None}
                    leaves = [rid for rid in remaining if rid not in referenced]
                    if not leaves:  # defensive: cycle, break arbitrarily
                        leaves = [next(iter(remaining))]
                    for rid in leaves:
                        remaining.discard(rid)
                        ordered.append(rid)
                ts = _iso(_utcnow())
                for rid in ordered:
                    conn.execute("DELETE FROM resources WHERE id = ?", (rid,))
                    audit.record(
                        conn,
                        ts=ts,
                        actor=caller.user_id,
                        action=audit.ACTION_PURGE,
                        resource_id=rid,
                        detail=f"cutoff={cutoff}",
                    )
                purged.extend(ordered)
        return {"purged": len(purged), "resource_ids": purged}

    # ------------------------------------------------------------------
    # audit
    # ------------------------------------------------------------------
    def audit_log(self, caller: Caller, *, limit: int = 200) -> list[AuditEntry]:
        if not caller.is_admin:
            raise ForbiddenError("only admins may read the audit log")
        rows = self._db.query(
            "SELECT id, ts, actor, action, resource_id, detail"
            " FROM audit_log ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [
            AuditEntry(
                id=r["id"],
                ts=r["ts"],
                actor=r["actor"],
                action=r["action"],
                resource_id=r["resource_id"],
                detail=r["detail"],
            )
            for r in rows
        ]
