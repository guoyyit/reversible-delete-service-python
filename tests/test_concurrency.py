"""Concurrent delete/restore/read consistency (requirement 7)."""

from __future__ import annotations

import threading
from collections import Counter

import pytest

from app.errors import NotFoundError


def test_concurrent_deletes_single_final_state(service, alice):
    r = service.create(alice, name="r")
    barrier = threading.Barrier(8)

    def deleter():
        barrier.wait()
        service.delete(alice, r.id)

    threads = [threading.Thread(target=deleter) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert service.count() == 0
    assert service.count(include_deleted=True) == 1
    deleted = service.get(r.id, include_deleted=True)
    assert deleted.deleted_at is not None
    assert deleted.delete_root_id == r.id


def test_concurrent_delete_and_restore_consistent_reads(service, alice):
    """Every single read observes one coherent state, never a field mix."""
    r = service.create(alice, name="r", content="payload")
    child = service.create(alice, name="child", parent_id=r.id)
    stop = threading.Event()
    inconsistencies: list[str] = []

    def check_row(row, rid):
        # field-level coherence: delete metadata is all-or-nothing
        flags = [
            row.deleted_at is not None,
            row.deleted_by is not None,
            row.delete_root_id is not None,
        ]
        if any(flags) and not all(flags):
            inconsistencies.append(f"mixed delete fields on {rid}: {row}")

    def reader():
        while not stop.is_set():
            try:
                root = service.get(r.id)
            except NotFoundError:
                root = None
            if root is not None:
                check_row(root, r.id)
                # children() is a coherent snapshot of the association
                kids = service.children(r.id)
                for k in kids:
                    if k.parent_id != r.id:
                        inconsistencies.append(f"bad parent link: {k}")
                    check_row(k, k.id)
                    if k.deleted_at is not None:
                        inconsistencies.append("deleted child in live listing")

    def toggler():
        for _ in range(20):
            service.delete(alice, r.id)
            service.restore(alice, r.id)
        stop.set()

    readers = [threading.Thread(target=reader) for _ in range(4)]
    for t in readers:
        t.start()
    toggler()
    for t in readers:
        t.join()

    assert inconsistencies == []
    # final state is fully restored
    assert service.get(r.id).deleted_at is None
    assert service.get(child.id).deleted_at is None


def test_concurrent_create_same_name_exactly_one_wins(service, alice):
    r = service.create(alice, name="seed")
    service.delete(alice, r.id)  # free the name
    barrier = threading.Barrier(6)
    outcomes: Counter = Counter()
    lock = threading.Lock()

    def creator():
        barrier.wait()
        try:
            service.create(alice, name="seed")
        except Exception as exc:
            outcome = type(exc).__name__
        else:
            outcome = "created"
        with lock:
            outcomes[outcome] += 1

    threads = [threading.Thread(target=creator) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert outcomes["created"] == 1
    assert outcomes["ConflictError"] == 5
    assert service.count() == 1


def test_concurrent_recycle_and_delete_never_purges_early(service, alice, admin):
    """Recycling must never remove rows still inside the retention window."""
    stop = threading.Event()
    created_ids: list[str] = []
    lock = threading.Lock()

    def writer():
        while not stop.is_set():
            r = service.create(alice, name="w" + threading.current_thread().name)
            with lock:
                created_ids.append(r.id)
            service.delete(alice, r.id)

    def recycler():
        while not stop.is_set():
            service.recycle_expired(admin)  # now=real time: nothing expired

    writers = [threading.Thread(target=writer, name=str(i)) for i in range(3)]
    rec = threading.Thread(target=recycler)
    for t in writers:
        t.start()
    rec.start()
    for t in writers:
        t.join(timeout=5)
    stop.set()
    for t in writers:
        t.join()
    rec.join()

    # nothing was ever expired, so everything must still exist (soft-deleted)
    assert service.count(include_deleted=True) == len(created_ids)
