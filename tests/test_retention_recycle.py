"""Retention window and recycling (requirements 5, 6)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.auth import Caller
from app.config import Settings
from app.db import Database
from app.errors import ConflictError, ForbiddenError
from app.service import ResourceService

RETENTION = 3600  # must match conftest.RETENTION


def future(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def test_restore_within_retention_succeeds(service, alice):
    r = service.create(alice, name="r")
    service.delete(alice, r.id)
    restored = service.restore(alice, r.id)  # well within RETENTION
    assert restored.deleted_at is None


def test_restore_after_retention_rejected(service, alice, settings, monkeypatch):
    r = service.create(alice, name="r")
    service.delete(alice, r.id)

    # jump beyond the retention window
    real_now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        "app.service._utcnow",
        lambda: real_now + timedelta(seconds=RETENTION + 1),
    )
    with pytest.raises(ConflictError) as exc:
        service.restore(alice, r.id)
    assert exc.value.code == "retention_expired"


def test_restore_at_retention_boundary_allowed(service, alice, monkeypatch):
    r = service.create(alice, name="r")
    service.delete(alice, r.id)
    real_now = datetime.now(timezone.utc)
    # exactly at the edge of the window: still restorable
    monkeypatch.setattr(
        "app.service._utcnow",
        lambda: real_now + timedelta(seconds=RETENTION - 1),
    )
    assert service.restore(alice, r.id).deleted_at is None


def test_recycle_skips_resources_within_retention(service, alice, admin):
    r = service.create(alice, name="r")
    service.delete(alice, r.id)
    # not yet expired
    assert service.recycle_expired(admin)["purged"] == 0
    assert service.get(r.id, include_deleted=True).deleted_at is not None
    # expired only just now -> still within window at t+RETENTION-1
    assert service.recycle_expired(admin, now=future(RETENTION - 1))["purged"] == 0
    assert service.count(include_deleted=True) == 1


def test_recycle_purges_after_retention(service, alice, admin):
    r = service.create(alice, name="r")
    service.delete(alice, r.id)
    result = service.recycle_expired(admin, now=future(RETENTION + 1))
    assert result["purged"] == 1
    assert result["resource_ids"] == [r.id]
    with pytest.raises(Exception):
        service.get(r.id, include_deleted=True)


def test_recycle_is_idempotent(service, alice, admin):
    r = service.create(alice, name="r")
    service.delete(alice, r.id)
    when = future(RETENTION + 1)
    assert service.recycle_expired(admin, now=when)["purged"] == 1
    assert service.recycle_expired(admin, now=when)["purged"] == 0
    assert service.recycle_expired(admin, now=when)["purged"] == 0


def test_recycle_purges_whole_subtree_no_orphans(service, alice, admin):
    root = service.create(alice, name="root")
    child = service.create(alice, name="child", parent_id=root.id)
    service.create(alice, name="gc", parent_id=child.id)
    service.delete(alice, root.id)

    result = service.recycle_expired(admin, now=future(RETENTION + 1))
    assert result["purged"] == 3
    assert service.count(include_deleted=True) == 0


def test_recycle_requires_admin(service, alice):
    with pytest.raises(ForbiddenError):
        service.recycle_expired(alice)


def test_recycle_batches_and_recovers_after_restart(tmp_path, settings):
    """Interrupted recycling leaves no half-deleted state and is reentrant."""
    db_file = str(tmp_path / "data.db")
    file_settings = Settings(
        db_path=db_file,
        retention_seconds=settings.retention_seconds,
        admin_users=settings.admin_users,
        recycle_batch_size=2,  # force multiple batches
    )
    alice = Caller("alice", False)
    admin = Caller("admin", True)

    db = Database(db_file)
    svc = ResourceService(db, file_settings)
    ids = [svc.create(alice, name=f"r{i}").id for i in range(5)]
    for rid in ids:
        svc.delete(alice, rid)
    db.close()  # simulate shutdown before recycling

    # restart: soft-deleted rows survive the restart (no half-deleted state)
    db2 = Database(db_file)
    svc2 = ResourceService(db2, file_settings)
    assert svc2.count(include_deleted=True) == 5
    assert svc2.count() == 0

    when = future(RETENTION + 1)
    # batch size 2 -> multiple passes inside one call, all committed per batch
    assert svc2.recycle_expired(admin, now=when)["purged"] == 5
    db2.close()

    # restart again: purged rows are gone for good, rerun is a no-op
    db3 = Database(db_file)
    svc3 = ResourceService(db3, file_settings)
    assert svc3.count(include_deleted=True) == 0
    assert svc3.recycle_expired(admin, now=when)["purged"] == 0
    db3.close()


def test_recycle_interrupted_mid_batch_is_reentrant(tmp_path, settings, monkeypatch):
    """A crash mid-recycle rolls back the open batch; rerun completes."""
    db_file = str(tmp_path / "data.db")
    file_settings = Settings(
        db_path=db_file,
        retention_seconds=settings.retention_seconds,
        admin_users=settings.admin_users,
        recycle_batch_size=10,
    )
    alice = Caller("alice", False)
    admin = Caller("admin", True)

    db = Database(db_file)
    svc = ResourceService(db, file_settings)
    root = svc.create(alice, name="root")
    child = svc.create(alice, name="child", parent_id=root.id)
    svc.delete(alice, root.id)

    # crash inside the first purge batch (after DELETE, before COMMIT)
    import app.audit as audit_mod

    class Boom(Exception):
        pass

    real_record = audit_mod.record
    state = {"armed": True}

    def flaky_record(conn, **kwargs):
        if state["armed"] and kwargs.get("action") == audit_mod.ACTION_PURGE:
            state["armed"] = False
            raise Boom("simulated crash")
        return real_record(conn, **kwargs)

    monkeypatch.setattr(audit_mod, "record", flaky_record)

    when = future(RETENTION + 1)
    with pytest.raises(Boom):
        svc.recycle_expired(admin, now=when)

    # transaction rolled back: rows are intact, still soft-deleted
    assert svc.count(include_deleted=True) == 2
    assert svc.get(root.id, include_deleted=True).deleted_at is not None
    assert svc.get(child.id, include_deleted=True).deleted_at is not None

    # rerun completes cleanly
    monkeypatch.undo()
    assert svc.recycle_expired(admin, now=when)["purged"] == 2
    assert svc.count(include_deleted=True) == 0
    db.close()
