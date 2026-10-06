"""可逆删除与恢复的核心语义。"""
import pytest

from app.errors import ConflictError, NotFoundError


def test_delete_hides_from_get_list_count(service, alice):
    r = service.create("a", "alice")
    service.delete(r.id, alice)

    with pytest.raises(NotFoundError):
        service.get(r.id, alice)
    assert service.list(alice) == []
    assert service.count(alice) == 0
    # 拥有者显式 include_deleted 可见
    assert service.get(r.id, alice, include_deleted=True).deleted is True
    assert len(service.list(alice, include_deleted=True)) == 1


def test_restore_brings_back_exact_state(service, alice):
    r = service.create("a", "alice")
    child = service.create("c", "alice", parent_id=r.id)
    service.delete(r.id, alice)
    restored = service.restore(r.id, alice)

    assert restored.deleted is False
    assert restored.deleted_at is None and restored.deleted_by is None
    assert service.get(child.id, alice).deleted is False
    assert service.get(child.id, alice).parent_id == r.id
    assert service.count(alice) == 1


def test_repeated_delete_is_deterministic_not_found(service, alice):
    r = service.create("a", "alice")
    service.delete(r.id, alice)
    with pytest.raises(NotFoundError):
        service.delete(r.id, alice)
    with pytest.raises(NotFoundError):
        service.delete(r.id, alice)


def test_repeated_restore_is_deterministic_conflict(service, alice):
    r = service.create("a", "alice")
    service.delete(r.id, alice)
    service.restore(r.id, alice)
    with pytest.raises(ConflictError):
        service.restore(r.id, alice)


def test_delete_frees_name_for_recreate(service, alice):
    r = service.create("a", "alice")
    service.delete(r.id, alice)
    r2 = service.create("a", "alice")  # 同名重建成功
    assert r2.id != r.id


def test_restore_conflicts_with_recreated_same_name(service, alice):
    r = service.create("a", "alice")
    service.delete(r.id, alice)
    service.create("a", "alice")  # 名称被新资源占用
    with pytest.raises(ConflictError):
        service.restore(r.id, alice)
    # 冲突不静默覆盖：新资源仍是唯一存活的 "a"
    assert service.count(alice) == 1


def test_active_name_uniqueness_still_enforced(service, alice):
    service.create("a", "alice")
    with pytest.raises(ConflictError):
        service.create("a", "alice")
