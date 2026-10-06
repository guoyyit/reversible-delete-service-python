import pytest

from app.auth import Caller
from app.config import Settings
from app.service import ResourceService
from app.store import StateStore


class FakeClock:
    def __init__(self, start: float = 1_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def settings() -> Settings:
    return Settings(retention_seconds=100.0, recycle_interval_seconds=0)


@pytest.fixture
def service(settings, clock) -> ResourceService:
    return ResourceService(StateStore(), settings, time_fn=clock)


@pytest.fixture
def alice() -> Caller:
    return Caller("alice")


@pytest.fixture
def bob() -> Caller:
    return Caller("bob")


@pytest.fixture
def admin() -> Caller:
    return Caller("root", role="admin")
