"""业务逻辑：可逆删除、恢复、批量操作、保留期回收。"""
from __future__ import annotations

from app.auth import Caller
from app.config import Settings
from app.models import Resource
from app.store import StateStore


class ResourceService:
    def __init__(self, store: StateStore, settings: Settings):
        self._store = store
        self._settings = settings

    # ---- 查询 ----
    def get(self, resource_id: str, caller: Caller, include_deleted: bool = False) -> Resource:
        raise NotImplementedError

    def list(self, caller: Caller, parent_id: str | None = None, include_deleted: bool = False) -> list[Resource]:
        raise NotImplementedError

    def count(self, caller: Caller, parent_id: str | None = None) -> int:
        raise NotImplementedError

    # ---- 写操作 ----
    def create(self, name: str, owner_id: str, parent_id: str | None = None) -> Resource:
        raise NotImplementedError

    def delete(self, resource_id: str, caller: Caller) -> Resource:
        raise NotImplementedError

    def restore(self, resource_id: str, caller: Caller) -> Resource:
        raise NotImplementedError

    def batch_delete(self, ids: list[str], caller: Caller) -> list[Resource]:
        raise NotImplementedError

    def batch_restore(self, ids: list[str], caller: Caller) -> list[Resource]:
        raise NotImplementedError

    # ---- 回收 ----
    def recycle_expired(self, now: float | None = None) -> list[str]:
        """物理清除保留期到期的已删除资源，返回被清除的 id 列表。幂等、可重入。"""
        raise NotImplementedError
