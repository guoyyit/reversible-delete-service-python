"""权限校验与审计记录。"""
import pytest

from app.errors import ForbiddenError, NotFoundError


def test_non_owner_cannot_delete(service, alice, bob):
    r = service.create("a", "alice")
    with pytest.raises(ForbiddenError):
        service.delete(r.id, bob)
    assert service.get(r.id, alice).deleted is False


def test_non_owner_cannot_restore(service, alice, bob):
    r = service.create("a", "alice")
    service.delete(r.id, alice)
    with pytest.raises(ForbiddenError):
        service.restore(r.id, bob)
    assert service.get(r.id, alice, include_deleted=True).deleted is True


def test_admin_can_delete_and_restore_any(service, alice, admin):
    r = service.create("a", "alice")
    service.delete(r.id, admin)
    service.restore(r.id, admin)
    assert service.get(r.id, alice).deleted is False


def test_deleted_resource_invisible_to_non_owner(service, alice, bob):
    r = service.create("a", "alice")
    service.delete(r.id, alice)
    # 非拥有者即使 include_deleted 也看不到（不泄露存在性）
    with pytest.raises(NotFoundError):
        service.get(r.id, bob, include_deleted=True)
    assert service.list(bob, include_deleted=True) == []


def test_audit_trail_records_all_outcomes(service, alice, bob, admin):
    r = service.create("a", "alice")
    try:
        service.delete(r.id, bob)       # denied
    except ForbiddenError:
        pass
    service.delete(r.id, alice)         # success
    try:
        service.delete(r.id, alice)     # not_found（重复删除）
    except NotFoundError:
        pass
    service.restore(r.id, alice)        # success

    audit = [(a.actor_id, a.action, a.result) for a in service._store.audit]
    assert ("bob", "delete", "denied") in audit
    assert ("alice", "delete", "success") in audit
    assert ("alice", "delete", "not_found") in audit
    assert ("alice", "restore", "success") in audit
