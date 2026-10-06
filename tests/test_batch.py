"""批量删除/恢复的原子语义。"""
import pytest

from app.auth import Caller
from app.errors import ForbiddenError, NotFoundError


def test_batch_delete_all_or_nothing_on_missing_id(service, alice):
    a = service.create("a", "alice")
    b = service.create("b", "alice")
    with pytest.raises(NotFoundError):
        service.batch_delete([a.id, "missing-id", b.id], alice)
    # 整体失败：两个资源都仍然存活
    assert service.count(alice) == 2


def test_batch_delete_all_or_nothing_on_permission(service, alice, bob):
    a = service.create("a", "alice")
    b = service.create("b", "bob")
    with pytest.raises(ForbiddenError):
        service.batch_delete([a.id, b.id], alice)
    assert service.count(alice) == 2  # 没有任何一项被删除


def test_batch_delete_parent_and_child_together(service, alice):
    p = service.create("p", "alice")
    c = service.create("c", "alice", parent_id=p.id)
    service.batch_delete([p.id, c.id], alice)
    assert service.count(alice) == 0
    assert service.get(c.id, alice, include_deleted=True).deleted is True


def test_batch_restore_atomic_on_conflict(service, alice):
    a = service.create("a", "alice")
    b = service.create("b", "alice")
    service.batch_delete([a.id, b.id], alice)
    service.create("a", "alice")  # 占用 a 的名称
    with pytest.raises(Exception) as ei:
        service.batch_restore([a.id, b.id], alice)
    assert ei.value.status_code == 409
    # 整体失败：b 也不能被单独恢复
    assert service.get(b.id, alice, include_deleted=True).deleted is True
    assert service.count(alice) == 1


def test_batch_restore_parent_and_child_together(service, alice):
    p = service.create("p", "alice")
    c = service.create("c", "alice", parent_id=p.id)
    # 分别独立删除（非级联），再同批恢复
    service.delete(c.id, alice)
    service.delete(p.id, alice)
    service.batch_restore([c.id, p.id], alice)  # 乱序也应成功
    assert service.get(p.id, alice).deleted is False
    assert service.get(c.id, alice).deleted is False


def test_batch_restore_denied_for_others_resource(service, alice, bob):
    r = service.create("x", "bob")
    service.delete(r.id, bob)
    with pytest.raises(ForbiddenError):
        service.batch_restore([r.id], alice)
    assert service.get(r.id, bob, include_deleted=True).deleted is True
