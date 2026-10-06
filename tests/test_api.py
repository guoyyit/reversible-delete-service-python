"""HTTP API surface: compatibility, auth headers, batch, recycle."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def make_client(tmp_path, **overrides) -> TestClient:
    settings = Settings(
        admin_users=frozenset({"admin"}), retention_seconds=3600, **overrides
    )
    return TestClient(create_app(settings))


def test_legacy_root_endpoint_unchanged(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json() == {"Hello": "World"}


def test_full_delete_restore_cycle_over_http(client, as_alice):
    root = client.post("/resources", json={"name": "root"}, headers=as_alice).json()
    child = client.post(
        "/resources",
        json={"name": "child", "parent_id": root["id"], "content": "v1"},
        headers=as_alice,
    ).json()

    resp = client.delete(f"/resources/{root['id']}", params={"reason": "x"}, headers=as_alice)
    assert resp.status_code == 200
    assert resp.json()["deleted_at"] is not None

    assert client.get(f"/resources/{root['id']}").status_code == 404
    assert client.get("/resources").json() == []
    assert client.get("/stats").json() == {"count": 0}

    resp = client.post(f"/resources/{root['id']}/restore", headers=as_alice)
    assert resp.status_code == 200
    assert client.get("/stats").json() == {"count": 2}
    assert client.get(f"/resources/{child['id']}").json()["content"] == "v1"


def test_unauthenticated_and_forbidden_over_http(client, as_alice):
    r = client.post("/resources", json={"name": "r"}, headers=as_alice).json()

    assert client.delete(f"/resources/{r['id']}").status_code == 401
    assert client.post(f"/resources/{r['id']}/restore").status_code == 401
    resp = client.delete(f"/resources/{r['id']}", headers={"X-User-Id": "bob"})
    assert resp.status_code == 403
    assert resp.json()["code"] == "forbidden"


def test_batch_endpoints_distinguish_total_and_partial(client, as_alice):
    a = client.post("/resources", json={"name": "a"}, headers=as_alice).json()
    b = client.post("/resources", json={"name": "b"}, headers=as_alice).json()

    # atomic: overall failure, nothing applied
    resp = client.post(
        "/resources/batch-delete",
        json={"ids": [a["id"], "missing", b["id"]]},
        headers=as_alice,
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["applied"] is False and body["succeeded"] == []
    assert client.get("/stats").json()["count"] == 2

    # non-atomic: partial success is explicit
    resp = client.post(
        "/resources/batch-delete",
        json={"ids": [a["id"], "missing", b["id"]], "atomic": False},
        headers=as_alice,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["succeeded"] == [a["id"], b["id"]]
    assert len(body["failed"]) == 1
    assert client.get("/stats").json()["count"] == 0

    # atomic restore succeeds as a whole
    resp = client.post(
        "/resources/batch-restore",
        json={"ids": [a["id"], b["id"]]},
        headers=as_alice,
    )
    assert resp.status_code == 200 and resp.json()["applied"] is True
    assert client.get("/stats").json()["count"] == 2


def test_recycle_endpoint_requires_admin(client, as_alice, as_admin):
    r = client.post("/resources", json={"name": "r"}, headers=as_alice).json()
    client.delete(f"/resources/{r['id']}", headers=as_alice)

    assert client.post("/admin/recycle", headers=as_alice).status_code == 403
    resp = client.post("/admin/recycle", headers=as_admin)
    assert resp.status_code == 200
    assert resp.json()["purged"] == 0  # still within retention


def test_audit_endpoint_over_http(client, as_alice, as_admin):
    r = client.post("/resources", json={"name": "r"}, headers=as_alice).json()
    client.delete(f"/resources/{r['id']}", headers=as_alice)

    assert client.get("/audit", headers=as_alice).status_code == 403
    entries = client.get("/audit", headers=as_admin).json()
    assert any(e["action"] == "delete" and e["resource_id"] == r["id"] for e in entries)


def test_restart_recovers_deleted_state(tmp_path):
    """Service restart must not resurrect or lose soft-deleted rows."""
    db_file = str(tmp_path / "restart.db")
    client = make_client(tmp_path, db_path=db_file)
    h = {"X-User-Id": "alice"}
    admin = {"X-User-Id": "admin"}

    live = client.post("/resources", json={"name": "live"}, headers=h).json()
    gone = client.post("/resources", json={"name": "gone"}, headers=h).json()
    client.delete(f"/resources/{gone['id']}", headers=h)

    # simulate restart: new app instance on the same database file
    client2 = make_client(tmp_path, db_path=db_file)
    assert client2.get("/stats").json()["count"] == 1
    assert client2.get(f"/resources/{live['id']}").status_code == 200
    assert client2.get(f"/resources/{gone['id']}").status_code == 404
    deleted = client2.get(
        f"/resources/{gone['id']}", params={"include_deleted": True}
    ).json()
    assert deleted["deleted_at"] is not None

    # restore still works after restart
    resp = client2.post(f"/resources/{gone['id']}/restore", headers=h)
    assert resp.status_code == 200
    assert client2.get("/stats").json()["count"] == 2


def test_background_recycler_purges_expired(tmp_path):
    """RDS_RECYCLE_INTERVAL_SECONDS > 0 starts a background recycle loop."""
    import time

    settings = Settings(
        admin_users=frozenset({"admin"}),
        retention_seconds=0,  # everything deleted is immediately expired
        recycle_interval_seconds=1,
    )
    with TestClient(create_app(settings)) as client:
        h = {"X-User-Id": "alice"}
        r = client.post("/resources", json={"name": "r"}, headers=h).json()
        client.delete(f"/resources/{r['id']}", headers=h)

        deadline = time.time() + 5
        while time.time() < deadline:
            gone = client.get(
                "/stats", params={"include_deleted": True}
            ).json()["count"] == 0
            if gone:
                break
            time.sleep(0.1)
        assert gone, "background recycler did not purge the expired resource"
