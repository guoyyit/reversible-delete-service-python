from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.auth import Caller
from app.config import Settings
from app.db import Database
from app.main import create_app
from app.service import ResourceService

RETENTION = 3600  # 1 hour for tests


@pytest.fixture()
def settings() -> Settings:
    return Settings(
        retention_seconds=RETENTION,
        admin_users=frozenset({"admin"}),
        recycle_batch_size=2,  # small to exercise multi-batch recycling
    )


@pytest.fixture()
def db():
    database = Database()
    yield database
    database.close()


@pytest.fixture()
def service(db, settings) -> ResourceService:
    return ResourceService(db, settings)


@pytest.fixture()
def alice() -> Caller:
    return Caller("alice", False)


@pytest.fixture()
def bob() -> Caller:
    return Caller("bob", False)


@pytest.fixture()
def admin() -> Caller:
    return Caller("admin", True)


@pytest.fixture()
def client(settings) -> TestClient:
    return TestClient(create_app(settings))


@pytest.fixture()
def as_alice() -> dict:
    return {"X-User-Id": "alice"}


@pytest.fixture()
def as_admin() -> dict:
    return {"X-User-Id": "admin"}
