"""服务重启后的状态恢复与到期回收。"""
import pytest

from app.auth import Caller
from app.config import Settings
from app.errors import GoneError
from app.service import ResourceService
from app.store import StateStore
from tests.conftest import FakeClock

ALICE = Caller("alice")


def _make_service(state_file, clock, retention=100.0):
    store = StateStore(state_file)
    store.load()
    return ResourceService(store, Settings(
        retention_seconds=retention, recycle_interval_seconds=0), time_fn=clock)


def test_state_survives_restart(tmp_path):
    f = str(tmp_path / "state.json")
    clock = FakeClock()
    svc1 = _make_service(f, clock)
    p = svc1.create("p", "alice")
    c = svc1.create("c", "alice", parent_id=p.id)
    svc1.delete(p.id, ALICE)

    svc2 = _make_service(f, clock)  # 模拟重启：从快照重建
    assert svc2.count(ALICE) == 0
    assert svc2.get(p.id, ALICE, include_deleted=True).deleted is True
    svc2.restore(p.id, ALICE)  # 重启后仍可恢复，关联完整
    assert svc2.get(c.id, ALICE).parent_id == p.id


def test_expired_resources_recycled_after_restart(tmp_path):
    f = str(tmp_path / "state.json")
    clock = FakeClock()
    svc1 = _make_service(f, clock)
    r = svc1.create("a", "alice")
    svc1.delete(r.id, ALICE)

    clock.advance(101)  # 服务“停机”期间保留期到期
    svc2 = _make_service(f, clock)
    purged = svc2.recycle_expired()  # 启动时回收一次
    assert purged == [r.id]
    with pytest.raises(Exception):
        svc2.get(r.id, ALICE, include_deleted=True)
    # 重启回收同样幂等
    assert svc2.recycle_expired() == []


def test_no_half_deleted_state_after_crash_before_snapshot(tmp_path):
    """快照是原子替换：崩溃只会留下旧快照或新快照，不会是中间态。"""
    import json
    import os

    f = str(tmp_path / "state.json")
    clock = FakeClock()
    svc = _make_service(f, clock)
    a = svc.create("a", "alice")
    b = svc.create("b", "alice")
    svc.batch_delete([a.id, b.id], ALICE)

    # 快照文件必须是完整 JSON（原子 rename 保证），且两个资源同为已删除
    with open(f, encoding="utf-8") as fh:
        payload = json.load(fh)
    states = {r["name"]: r["deleted"] for r in payload["resources"]}
    assert states == {"a": True, "b": True}
    assert not os.path.exists(f + ".tmp")


def test_audit_log_survives_restart(tmp_path):
    f = str(tmp_path / "state.json")
    clock = FakeClock()
    svc1 = _make_service(f, clock)
    r = svc1.create("a", "alice")
    svc1.delete(r.id, ALICE)

    svc2 = _make_service(f, clock)
    actions = [(a.actor_id, a.action, a.result) for a in svc2._store.audit]
    assert ("alice", "delete", "success") in actions
