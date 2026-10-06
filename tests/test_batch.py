"""Batch delete/restore atomicity (requirement 4)."""

from __future__ import annotations

import pytest

from app.service import BatchConflictError


def test_atomic_delete_batch_all_or_nothing(service, alice):
    a = service.create(alice, name="a")
    b = service.create(alice, name="b")

    with pytest.raises(BatchConflictError) as exc:
        service.delete_batch(alice, [a.id, "missing", b.id])

    payload = exc.value.payload
    assert payload["applied"] is False
    assert payload["succeeded"] == []
    assert [f["id"] for f in payload["failed"]] == ["missing"]
    # nothing was applied: both resources still alive
    assert service.count() == 2
    assert service.get(a.id).deleted_at is None
    assert service.get(b.id).deleted_at is None


def test_atomic_delete_batch_success(service, alice):
    a = service.create(alice, name="a")
    b = service.create(alice, name="b")
    result = service.delete_batch(alice, [a.id, b.id])
    assert result == {
        "atomic": True,
        "applied": True,
        "succeeded": [a.id, b.id],
        "failed": [],
    }
    assert service.count() == 0


def test_atomic_restore_batch_rolls_back_on_conflict(service, alice):
    a = service.create(alice, name="a")
    b = service.create(alice, name="b")
    service.delete(alice, a.id)
    service.delete(alice, b.id)
    # recreate a blocker with name 'a' so restoring a must conflict
    service.create(alice, name="a")

    with pytest.raises(BatchConflictError):
        service.restore_batch(alice, [a.id, b.id])

    # whole batch rolled back: b was NOT restored either
    assert service.get(a.id, include_deleted=True).deleted_at is not None
    assert service.get(b.id, include_deleted=True).deleted_at is not None
    assert service.count() == 1  # only the blocker


def test_non_atomic_batch_reports_partial_success(service, alice):
    a = service.create(alice, name="a")
    b = service.create(alice, name="b")

    result = service.delete_batch(alice, [a.id, "missing", b.id], atomic=False)

    assert result["atomic"] is False
    assert result["applied"] is False  # not fully applied
    assert result["succeeded"] == [a.id, b.id]
    assert [f["id"] for f in result["failed"]] == ["missing"]
    # partial state is explicit and explainable
    assert service.get(a.id, include_deleted=True).deleted_at is not None
    assert service.get(b.id, include_deleted=True).deleted_at is not None
    assert service.count() == 0


def test_non_atomic_restore_batch_partial(service, alice):
    a = service.create(alice, name="a")
    b = service.create(alice, name="b")
    service.delete(alice, a.id)
    service.delete(alice, b.id)
    service.create(alice, name="a")  # blocks restore of a

    result = service.restore_batch(alice, [a.id, b.id], atomic=False)

    assert result["succeeded"] == [b.id]
    assert [f["id"] for f in result["failed"]] == [a.id]
    assert result["failed"][0]["code"] == "name_conflict"
    assert service.get(b.id).deleted_at is None
    assert service.get(a.id, include_deleted=True).deleted_at is not None


def test_atomic_batch_permission_failure_applies_nothing(service, alice, bob):
    mine = service.create(alice, name="mine")
    yours = service.create(bob, name="yours")

    with pytest.raises(BatchConflictError) as exc:
        service.delete_batch(alice, [mine.id, yours.id])

    assert exc.value.payload["applied"] is False
    assert exc.value.payload["failed"][0]["code"] == "forbidden"
    assert service.count() == 2  # nothing deleted
