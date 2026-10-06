"""Reversible delete / restore semantics (requirement 1, 8: repeat ops)."""

from __future__ import annotations

import pytest

from app.errors import NotFoundError


def test_delete_hides_from_get_list_count_children(service, alice):
    root = service.create(alice, name="root")
    child = service.create(alice, name="child", parent_id=root.id, content="v1")

    service.delete(alice, root.id)

    with pytest.raises(NotFoundError):
        service.get(root.id)
    with pytest.raises(NotFoundError):
        service.get(child.id)
    assert service.list() == []
    assert service.count() == 0
    with pytest.raises(NotFoundError):
        service.children(root.id)
    # ...but rows are still there when explicitly including deleted
    assert service.count(include_deleted=True) == 2
    assert service.get(root.id, include_deleted=True).deleted_at is not None


def test_restore_brings_back_content_and_associations(service, alice):
    root = service.create(alice, name="root")
    child = service.create(alice, name="child", parent_id=root.id, content="v1")
    grandchild = service.create(alice, name="gc", parent_id=child.id)

    service.delete(alice, root.id)
    restored = service.restore(alice, root.id)

    assert restored.deleted_at is None
    assert restored.deleted_by is None
    assert restored.delete_reason is None
    assert service.get(child.id).content == "v1"
    assert service.get(grandchild.id).parent_id == child.id
    assert [c.id for c in service.children(root.id)] == [child.id]
    assert [c.id for c in service.children(child.id)] == [grandchild.id]
    assert service.count() == 3


def test_repeat_delete_is_idempotent(service, alice):
    root = service.create(alice, name="root")
    first = service.delete(alice, root.id, reason="r1")
    second = service.delete(alice, root.id, reason="r2")
    assert first.deleted_at == second.deleted_at  # timestamp unchanged
    assert second.deleted_by == "alice"
    assert service.count(include_deleted=True) == 1


def test_repeat_restore_is_idempotent(service, alice):
    root = service.create(alice, name="root")
    service.delete(alice, root.id)
    first = service.restore(alice, root.id)
    second = service.restore(alice, root.id)
    assert first.deleted_at is None and second.deleted_at is None
    assert service.count() == 1


def test_restore_of_never_deleted_resource_is_noop(service, alice):
    root = service.create(alice, name="root")
    restored = service.restore(alice, root.id)
    assert restored.deleted_at is None


def test_delete_records_reason_and_actor(service, alice):
    root = service.create(alice, name="root")
    deleted = service.delete(alice, root.id, reason="cleanup")
    assert deleted.deleted_by == "alice"
    assert deleted.delete_reason == "cleanup"
    assert deleted.delete_root_id == root.id
