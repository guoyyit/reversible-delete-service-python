"""Authorization and audit trail (requirement 5)."""

from __future__ import annotations

import pytest

from app.errors import ForbiddenError, UnauthorizedError
from app.auth import resolve_caller


def test_delete_requires_ownership_or_admin(service, alice, bob, admin):
    r = service.create(alice, name="r")

    with pytest.raises(ForbiddenError):
        service.delete(bob, r.id)
    # still alive after the denied attempt
    assert service.get(r.id).deleted_at is None

    service.delete(admin, r.id)  # admin may delete anyone's resource
    assert service.get(r.id, include_deleted=True).deleted_at is not None


def test_restore_requires_ownership_or_admin(service, alice, bob, admin):
    r = service.create(alice, name="r")
    service.delete(alice, r.id)

    with pytest.raises(ForbiddenError):
        service.restore(bob, r.id)
    assert service.get(r.id, include_deleted=True).deleted_at is not None

    service.restore(admin, r.id)
    assert service.get(r.id).deleted_at is None


def test_denied_attempts_are_audited(service, alice, bob, admin):
    r = service.create(alice, name="r")
    with pytest.raises(ForbiddenError):
        service.delete(bob, r.id)
    with pytest.raises(ForbiddenError):
        service.restore(bob, r.id)

    entries = service.audit_log(admin)
    denied = [e for e in entries if e.action in ("delete_denied", "restore_denied")]
    assert len(denied) == 2
    assert all(e.actor == "bob" for e in denied)
    assert all(e.resource_id == r.id for e in denied)


def test_successful_operations_are_audited(service, alice, admin):
    r = service.create(alice, name="r")
    service.delete(alice, r.id, reason="cleanup")
    service.restore(alice, r.id)

    entries = service.audit_log(admin)
    by_action = {e.action: e for e in entries}
    assert by_action["delete"].actor == "alice"
    assert by_action["delete"].resource_id == r.id
    assert "cleanup" in by_action["delete"].detail
    assert by_action["restore"].actor == "alice"


def test_restore_conflict_is_audited(service, alice, admin):
    old = service.create(alice, name="r")
    service.delete(alice, old.id)
    service.create(alice, name="r")  # blocker

    with pytest.raises(Exception):
        service.restore(alice, old.id)

    entries = service.audit_log(admin)
    conflicts = [e for e in entries if e.action == "restore_conflict"]
    assert len(conflicts) == 1
    assert conflicts[0].resource_id == old.id


def test_purge_is_audited(service, alice, admin):
    from datetime import datetime, timedelta, timezone

    r = service.create(alice, name="r")
    service.delete(alice, r.id)
    when = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    service.recycle_expired(admin, now=when)

    entries = service.audit_log(admin)
    purges = [e for e in entries if e.action == "purge"]
    assert len(purges) == 1
    assert purges[0].actor == "admin"
    assert purges[0].resource_id == r.id


def test_audit_log_requires_admin(service, alice):
    with pytest.raises(ForbiddenError):
        service.audit_log(alice)


def test_missing_identity_rejected(settings):
    with pytest.raises(UnauthorizedError):
        resolve_caller(None, settings)
    with pytest.raises(UnauthorizedError):
        resolve_caller("", settings)


def test_admin_flag_resolution(settings):
    assert resolve_caller("admin", settings).is_admin
    assert not resolve_caller("alice", settings).is_admin
