"""HTTP 端到端：接口契约、鉴权头、批量语义、回收端点。"""
import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.config import Settings

ALICE = {"X-User-Id": "alice"}
BOB = {"X-User-Id": "bob"}
ADMIN = {"X-User-Id": "root", "X-User-Role": "admin"}


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        retention_seconds=100.0,
        state_file=str(tmp_path / "state.json"),
        recycle_interval_seconds=0,
    )
    return TestClient(create_app(settings))


def test_missing_identity_header_rejected(client):
    assert client.get("/resources").status_code == 401


def test_full_lifecycle_over_http(client):
    r = client.post("/resources", json={"name": "a"}, headers=ALICE)
    assert r.status_code == 201
    rid = r.json()["id"]

    assert client.get("/resources", headers=ALICE).json()["count"] == 1
    assert client.delete(f"/resources/{rid}", headers=ALICE).status_code == 200
    # 删除后默认查询消失（既有契约不变）
    assert client.get(f"/resources/{rid}", headers=ALICE).status_code == 404
    assert client.get("/resources", headers=ALICE).json()["count"] == 0
    # 恢复后内容可见且一致
    assert client.post(f"/resources/{rid}/restore", headers=ALICE).status_code == 200
    assert client.get(f"/resources/{rid}", headers=ALICE).json()["name"] == "a"


def test_restore_conflict_returns_409(client):
    rid = client.post("/resources", json={"name": "a"}, headers=ALICE).json()["id"]
    client.delete(f"/resources/{rid}", headers=ALICE)
    client.post("/resources", json={"name": "a"}, headers=ALICE)
    resp = client.post(f"/resources/{rid}/restore", headers=ALICE)
    assert resp.status_code == 409


def test_batch_delete_atomic_failure_message(client):
    a = client.post("/resources", json={"name": "a"}, headers=ALICE).json()["id"]
    resp = client.post("/resources/batch-delete",
                       json={"ids": [a, "missing"]}, headers=ALICE)
    assert resp.status_code == 404
    assert "batch aborted" in resp.json()["detail"]
    # 整体失败：a 仍然存活
    assert client.get(f"/resources/{a}", headers=ALICE).status_code == 200


def test_batch_restore_success(client):
    ids = [client.post("/resources", json={"name": n}, headers=ALICE).json()["id"]
           for n in ("a", "b")]
    assert client.post("/resources/batch-delete", json={"ids": ids},
                       headers=ALICE).status_code == 200
    resp = client.post("/resources/batch-restore", json={"ids": ids}, headers=ALICE)
    assert resp.status_code == 200
    assert len(resp.json()["restored"]) == 2


def test_forbidden_over_http(client):
    rid = client.post("/resources", json={"name": "a"}, headers=ALICE).json()["id"]
    assert client.delete(f"/resources/{rid}", headers=BOB).status_code == 403


def test_recycle_endpoint_admin_only(client):
    assert client.post("/admin/recycle", headers=ALICE).status_code == 403
    assert client.post("/admin/recycle", headers=ADMIN).json() == {"purged": []}


def test_audit_endpoint_admin_only(client):
    rid = client.post("/resources", json={"name": "a"}, headers=ALICE).json()["id"]
    client.delete(f"/resources/{rid}", headers=ALICE)
    assert client.get("/audit", headers=ALICE).status_code == 403
    items = client.get("/audit", headers=ADMIN).json()["items"]
    assert any(a["action"] == "delete" and a["result"] == "success" for a in items)
