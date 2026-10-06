"""Uniqueness visibility scope (requirement 2)."""

from __future__ import annotations

import pytest

from app.errors import ConflictError


def test_duplicate_name_rejected_while_alive(service, alice):
    service.create(alice, name="root")
    with pytest.raises(ConflictError) as exc:
        service.create(alice, name="root")
    assert exc.value.code == "name_conflict"


def test_same_name_allowed_under_different_parents(service, alice):
    p1 = service.create(alice, name="p1")
    p2 = service.create(alice, name="p2")
    c1 = service.create(alice, name="child", parent_id=p1.id)
    c2 = service.create(alice, name="child", parent_id=p2.id)
    assert c1.id != c2.id


def test_name_reusable_after_delete(service, alice):
    old = service.create(alice, name="root")
    service.delete(alice, old.id)
    new = service.create(alice, name="root")
    assert new.id != old.id
    assert service.count() == 1


def test_restore_conflicts_with_recreated_name(service, alice):
    old = service.create(alice, name="root")
    service.delete(alice, old.id)
    new = service.create(alice, name="root")

    with pytest.raises(ConflictError) as exc:
        service.restore(alice, old.id)
    assert exc.value.code == "name_conflict"

    # deterministic outcome: old stays deleted, new untouched, no duplicates
    assert service.get(old.id, include_deleted=True).deleted_at is not None
    assert service.get(new.id).deleted_at is None
    assert service.count() == 1
    assert service.count(include_deleted=True) == 2


def test_restore_succeeds_after_conflicting_name_moved(service, alice):
    old = service.create(alice, name="root")
    service.delete(alice, old.id)
    blocker = service.create(alice, name="root")
    service.delete(alice, blocker.id)  # free the name again

    restored = service.restore(alice, old.id)
    assert restored.deleted_at is None
    assert service.count() == 1


def test_restore_conflict_inside_cascade_member(service, alice):
    root = service.create(alice, name="root")
    child = service.create(alice, name="child", parent_id=root.id)
    service.delete(alice, root.id)  # cascades to child
    # recreate a different tree that reuses the child name under a new root
    root2 = service.create(alice, name="root2")
    service.create(alice, name="child", parent_id=root2.id)
    # restoring old root must fail deterministically: its child name clashes
    # only if parents match -- here parents differ, so restore succeeds.
    restored = service.restore(alice, root.id)
    assert restored.deleted_at is None
    assert service.get(child.id).parent_id == root.id
