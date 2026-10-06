"""父资源删除/恢复时关联资源的可见性与可恢复性。"""
import pytest

from app.errors import ConflictError, NotFoundError


def test_delete_parent_hides_descendants(service, alice):
    p = service.create("p", "alice")
    c = service.create("c", "alice", parent_id=p.id)
    g = service.create("g", "alice", parent_id=c.id)

    service.delete(p.id, alice)

    for rid in (p.id, c.id, g.id):
        with pytest.raises(NotFoundError):
            service.get(rid, alice)
    assert service.count(alice) == 0


def test_restore_parent_restores_whole_subtree(service, alice):
    p = service.create("p", "alice")
    c = service.create("c", "alice", parent_id=p.id)
    g = service.create("g", "alice", parent_id=c.id)

    service.delete(p.id, alice)
    service.restore(p.id, alice)

    assert service.get(c.id, alice).parent_id == p.id
    assert service.get(g.id, alice).parent_id == c.id
    assert service.count(alice) == 1  # 顶层只有 p
    assert service.count(alice, parent_id=p.id) == 1


def test_independently_deleted_child_stays_deleted_after_parent_restore(service, alice):
    p = service.create("p", "alice")
    c = service.create("c", "alice", parent_id=p.id)
    service.delete(c.id, alice)   # 子资源先被独立删除
    service.delete(p.id, alice)   # 父之后删除
    service.restore(p.id, alice)

    # 独立删除的子资源不随父恢复而复活，不产生计划外数据
    assert service.get(p.id, alice).deleted is False
    with pytest.raises(NotFoundError):
        service.get(c.id, alice)
    assert service.get(c.id, alice, include_deleted=True).deleted is True


def test_restore_child_while_parent_deleted_rejected(service, alice):
    p = service.create("p", "alice")
    c = service.create("c", "alice", parent_id=p.id)
    service.delete(p.id, alice)

    # 直接恢复级联子资源会破坏父链完整性，必须拒绝（不产生孤儿）
    with pytest.raises(ConflictError):
        service.restore(c.id, alice)
    assert service.get(c.id, alice, include_deleted=True).deleted is True


def test_cannot_create_under_deleted_parent(service, alice):
    p = service.create("p", "alice")
    service.delete(p.id, alice)
    with pytest.raises(NotFoundError):
        service.create("x", "alice", parent_id=p.id)


def test_no_duplicate_links_after_restore(service, alice):
    p = service.create("p", "alice")
    c = service.create("c", "alice", parent_id=p.id)
    service.delete(p.id, alice)
    service.restore(p.id, alice)
    children = service.list(alice, parent_id=p.id)
    assert [x.id for x in children] == [c.id]  # 关联关系恰好恢复一次
