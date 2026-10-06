"""Resource service: reversible delete, restore, retention recycling."""

from __future__ import annotations

from .auth import Caller
from .config import Settings
from .db import Database
from .models import AuditEntry, Resource


class ResourceService:
    def __init__(self, db: Database, settings: Settings):
        self._db = db
        self._settings = settings

    # -- CRUD -------------------------------------------------------------
    def create(
        self,
        caller: Caller,
        *,
        name: str,
        parent_id: str | None = None,
        content: str | None = None,
        owner_id: str | None = None,
    ) -> Resource:
        raise NotImplementedError

    def get(self, resource_id: str, *, include_deleted: bool = False) -> Resource:
        raise NotImplementedError

    def list(
        self,
        *,
        parent_id: str | None = None,
        include_deleted: bool = False,
    ) -> list[Resource]:
        raise NotImplementedError

    def children(self, parent_id: str, *, include_deleted: bool = False) -> list[Resource]:
        raise NotImplementedError

    def count(self, *, include_deleted: bool = False) -> int:
        raise NotImplementedError

    # -- reversible delete --------------------------------------------------
    def delete(self, caller: Caller, resource_id: str, *, reason: str | None = None) -> Resource:
        raise NotImplementedError

    def restore(self, caller: Caller, resource_id: str) -> Resource:
        raise NotImplementedError

    def delete_batch(
        self, caller: Caller, ids: list[str], *, atomic: bool = True, reason: str | None = None
    ) -> dict:
        raise NotImplementedError

    def restore_batch(self, caller: Caller, ids: list[str], *, atomic: bool = True) -> dict:
        raise NotImplementedError

    # -- retention recycling ------------------------------------------------
    def recycle_expired(self, caller: Caller, *, now: str | None = None) -> dict:
        raise NotImplementedError

    # -- audit ----------------------------------------------------------------
    def audit_log(self, caller: Caller, *, limit: int = 200) -> list[AuditEntry]:
        raise NotImplementedError
