"""保留期语义与到期回收。"""
import pytest

from app.errors import GoneError


def test_restore_within_retention_ok(service, alice, clock):
    r = service.create("a", "alice")
    service.delete(r.id, alice)
    clock.advance(99)  # 保留期 100s 内
    service.restore(r.id, alice)
    assert service.get(r.id, alice).deleted is False


def test_restore_at_retention_boundary_ok(service, alice, clock):
    r = service.create("a", "alice")
    service.delete(r.id, alice)
    clock.advance(100)  # 恰好等于保留期：仍可恢复（边界含）
    service.restore(r.id, alice)


def test_restore_after_retention_rejected(service, alice, clock):
    r = service.create("a", "alice")
    service.delete(r.id, alice)
    clock.advance(101)
    with pytest.raises(GoneError):
        service.restore(r.id, alice)
    # 审计可追溯
    kinds = [(a.action, a.result) for a in service._store.audit]
    assert ("restore", "expired") in kinds


def test_recycle_purges_only_expired(service, alice, clock):
    old = service.create("old", "alice")
    service.delete(old.id, alice)
    clock.advance(101)
    fresh = service.create("fresh", "alice")
    service.delete(fresh.id, alice)  # 刚删，仍在保留期内

    purged = service.recycle_expired()
    assert purged == [old.id]
    # 未到期资源绝不误删，且仍可恢复
    assert service.get(fresh.id, alice, include_deleted=True).deleted is True
    service.restore(fresh.id, alice)


def test_recycle_is_idempotent_and_resumable(service, alice, clock):
    r = service.create("a", "alice")
    service.delete(r.id, alice)
    clock.advance(101)
    first = service.recycle_expired()
    assert first == [r.id]
    # 重复执行结果一致（幂等），模拟中断后重入
    assert service.recycle_expired() == []
    assert service.recycle_expired() == []


def test_recycle_purges_cascade_members_together(service, alice, clock):
    p = service.create("p", "alice")
    c = service.create("c", "alice", parent_id=p.id)
    service.delete(p.id, alice)
    clock.advance(101)
    purged = service.recycle_expired()
    assert sorted(purged) == sorted([p.id, c.id])
    assert service._store.resources == {}


def test_recycle_leaves_active_resources_untouched(service, alice, clock):
    a = service.create("a", "alice")
    d = service.create("d", "alice")
    service.delete(d.id, alice)
    clock.advance(10_000)
    service.recycle_expired()
    assert service.get(a.id, alice).deleted is False
