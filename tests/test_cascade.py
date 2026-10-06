"""Parent/child visibility and recoverability (requirement 3)."""

from __future__ import annotations

import pytest

from app.errors import ConflictError, NotFoundError


def test_children_hidden_while_parent_deleted(service, alice):
    root = service.create(alice, name="root")
    child = service.create(alice, name="child", parent_id=root.id)
    gc = service.create(alice, name="gc", parent_id=child.id)

    service.delete(alice, root.id)

    # every descendant is deterministically invisible
    for rid in (root.id, child.id, gc.id):
        with pytest.raises(NotFoundError):
            service.get(rid)
    assert service.count() == 0


def test_restore_parent_restores_exact_cascade_set(service, alice):
    root = service.create(alice, name="root")
    child = service.create(alice, name="child", parent_id=root.id)
    gc = service.create(alice, name="gc", parent_id=child.id)
    # independently deleted before the cascade: must NOT come back
    other = service.create(alice, name="other", parent_id=root.id)
    service.delete(alice, other.id)

    service.delete(alice, root.id)
    service.restore(alice, root.id)

    assert service.get(child.id).deleted_at is None
    assert service.get(gc.id).deleted_at is None
    # 'other' was deleted on its own before the cascade: stays deleted
    assert service.get(other.id, include_deleted=True).deleted_at is not None
    assert service.count() == 3  # root, child, gc


def test_cascade_member_cannot_be_restored_directly(service, alice):
    root = service.create(alice, name="root")
    child = service.create(alice, name="child", parent_id=root.id)
    service.delete(alice, root.id)

    with pytest.raises(ConflictError) as exc:
        service.restore(alice, child.id)
    assert exc.value.code == "cascade_member"
    # state unchanged: still fully deleted, no orphan produced
    assert service.count() == 0
    assert service.count(include_deleted=True) == 2


def test_no_orphan_after_parent_restore(service, alice):
    root = service.create(alice, name="root")
    child = service.create(alice, name="child", parent_id=root.id)
    service.delete(alice, root.id)
    service.restore(alice, root.id)

    # every restored row is reachable from the root: no orphans
    assert [c.id for c in service.children(root.id)] == [child.id]
    assert service.get(child.id).parent_id == root.id


def test_cannot_create_child_under_deleted_parent(service, alice):
    root = service.create(alice, name="root")
    service.delete(alice, root.id)
    with pytest.raises(NotFoundError):
        service.create(alice, name="child", parent_id=root.id)


def test_independently_deleted_child_survives_parent_delete_restore(service, alice):
    root = service.create(alice, name="root")
    keep = service.create(alice, name="keep", parent_id=root.id)
    gone = service.create(alice, name="gone", parent_id=root.id)
    service.delete(alice, gone.id)  # independent delete

    service.delete(alice, root.id)
    service.restore(alice, root.id)

    assert service.get(keep.id).deleted_at is None
    assert service.get(gone.id, include_deleted=True).deleted_at is not None
    # and 'gone' keeps its own delete root (it is restorable on its own)
    assert service.get(gone.id, include_deleted=True).delete_root_id == gone.id
