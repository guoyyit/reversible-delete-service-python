"""业务逻辑：可逆删除、恢复、批量操作、保留期回收。

一致性约定：所有读-改-写都在 store.locked() 临界区内完成，
每次变更整体提交后才释放锁，对外只暴露单一的已删除/未删除状态。
"""
from __future__ import annotations

import time
import uuid
from typing import Callable

from app.auth import Caller
from app.config import Settings
from app.errors import ConflictError, ForbiddenError, GoneError, NotFoundError
from app.models import DeleteOrigin, Resource
from app.store import StateStore

SYSTEM_ACTOR = "system"


class ResourceService:
    def __init__(self, store: StateStore, settings: Settings,
                 time_fn: Callable[[], float] = time.time):
        self._store = store
        self._settings = settings
        self._now = time_fn

    # ================= 查询 =================

    def get(self, resource_id: str, caller: Caller, include_deleted: bool = False) -> Resource:
        with self._store.locked():
            r = self._store.resources.get(resource_id)
            if r is None:
                raise NotFoundError(f"resource {resource_id} not found")
            if r.deleted:
                if not include_deleted or not caller.can_operate(r.owner_id):
                    # 已删除资源对普通查询不可见，且不向无权者泄露存在性
                    raise NotFoundError(f"resource {resource_id} not found")
            return r

    def list(self, caller: Caller, parent_id: str | None = None,
             include_deleted: bool = False) -> list[Resource]:
        with self._store.locked():
            out = []
            for r in self._store.resources.values():
                if r.parent_id != parent_id:
                    continue
                if not r.deleted:
                    out.append(r)
                elif include_deleted and caller.can_operate(r.owner_id):
                    out.append(r)
            return sorted(out, key=lambda r: r.created_at)

    def count(self, caller: Caller, parent_id: str | None = None) -> int:
        with self._store.locked():
            return sum(
                1 for r in self._store.resources.values()
                if r.parent_id == parent_id and not r.deleted
            )

    # ================= 创建 =================

    def create(self, name: str, owner_id: str, parent_id: str | None = None) -> Resource:
        with self._store.locked():
            if parent_id is not None:
                parent = self._store.resources.get(parent_id)
                if parent is None or parent.deleted:
                    # 已删除的父资源不可见，等同于不存在
                    raise NotFoundError(f"parent {parent_id} not found")
            self._ensure_name_free(parent_id, name)
            r = Resource(
                id=uuid.uuid4().hex,
                name=name,
                owner_id=owner_id,
                parent_id=parent_id,
                created_at=self._now(),
            )
            self._store.resources[r.id] = r
            self._store.snapshot()
            return r

    # ================= 删除 =================

    def delete(self, resource_id: str, caller: Caller) -> Resource:
        with self._store.locked():
            r = self._store.resources.get(resource_id)
            if r is None or r.deleted:
                self._audit(caller, "delete", resource_id, "not_found", "absent or already deleted")
                raise NotFoundError(f"resource {resource_id} not found")
            self._check_permission(caller, r, "delete")
            self._apply_delete(r, caller, DeleteOrigin.DIRECT, r.id)
            self._audit(caller, "delete", r.id, "success", f"cascade_root={r.id}")
            self._store.snapshot()
            return r

    def _apply_delete(self, root: Resource, caller: Caller,
                      origin: DeleteOrigin, cascade_root_id: str) -> None:
        """软删除 root 及其全部存活后代（同事务内完成）。"""
        now = self._now()
        root.deleted = True
        root.deleted_at = now
        root.deleted_by = caller.user_id
        root.delete_origin = origin
        root.cascade_root_id = cascade_root_id
        root.version += 1
        for child in self._children_of(root.id):
            if not child.deleted:
                self._apply_delete(child, caller, DeleteOrigin.CASCADE, cascade_root_id)

    # ================= 恢复 =================

    def restore(self, resource_id: str, caller: Caller) -> Resource:
        with self._store.locked():
            r = self._store.resources.get(resource_id)
            if r is None:
                self._audit(caller, "restore", resource_id, "not_found", "absent")
                raise NotFoundError(f"resource {resource_id} not found")
            if not r.deleted:
                self._audit(caller, "restore", r.id, "conflict", "resource is not deleted")
                raise ConflictError(f"resource {resource_id} is not deleted")
            self._check_permission(caller, r, "restore")
            self._check_retention(r)
            members = self._restore_closure(r)
            self._validate_restore(members)
            for m in members:
                self._apply_restore(m)
            self._audit(caller, "restore", r.id, "success",
                        f"restored {len(members)} resource(s)")
            self._store.snapshot()
            return r

    def _restore_closure(self, root: Resource) -> list[Resource]:
        """待恢复集合：root 本身 + 由同一次级联删除带走的后代。"""
        members = [root]
        if root.cascade_root_id == root.id:
            members.extend(
                r for r in self._store.resources.values()
                if r.deleted
                and r.delete_origin == DeleteOrigin.CASCADE
                and r.cascade_root_id == root.id
            )
        return members

    def _validate_restore(self, members: list[Resource]) -> None:
        """恢复前校验：父链完整、名称不冲突。任一失败则整体不生效。"""
        member_ids = {m.id for m in members}
        for m in members:
            if m.parent_id is not None and m.parent_id not in member_ids:
                parent = self._store.resources.get(m.parent_id)
                if parent is None or parent.deleted:
                    self._audit(SYSTEM_ACTOR, "restore", m.id, "conflict",
                                "parent is deleted or gone")
                    raise ConflictError(
                        f"parent of {m.id} is deleted; restore the parent first")
            self._ensure_name_free(m.parent_id, m.name, exclude_ids=member_ids,
                                   audit_id=m.id)

    def _apply_restore(self, r: Resource) -> None:
        r.deleted = False
        r.deleted_at = None
        r.deleted_by = None
        r.delete_origin = None
        r.cascade_root_id = None
        r.version += 1

    # ================= 批量操作（原子：全部校验通过后一次性生效） =================

    def batch_delete(self, ids: list[str], caller: Caller) -> list[Resource]:
        with self._store.locked():
            targets = []
            for rid in ids:
                r = self._store.resources.get(rid)
                if r is None or r.deleted:
                    self._audit(caller, "batch_delete", rid, "not_found",
                                "batch aborted, nothing applied")
                    raise NotFoundError(f"resource {rid} not found; batch aborted")
                if not caller.can_operate(r.owner_id):
                    self._audit(caller, "batch_delete", rid, "denied",
                                "batch aborted, nothing applied")
                    raise ForbiddenError(f"not allowed to delete {rid}; batch aborted")
                targets.append(r)
            deleted: dict[str, Resource] = {}
            for r in targets:
                if not r.deleted:  # 同批中可能已被上游级联带走
                    self._apply_delete(r, caller, DeleteOrigin.DIRECT, r.id)
                deleted[r.id] = r
            self._audit(caller, "batch_delete", None, "success",
                        f"deleted roots: {sorted(deleted)}")
            self._store.snapshot()
            return list(deleted.values())

    def batch_restore(self, ids: list[str], caller: Caller) -> list[Resource]:
        with self._store.locked():
            # 先按深度排序，保证父先于子校验/恢复
            requested = []
            for rid in ids:
                r = self._store.resources.get(rid)
                if r is None:
                    self._audit(caller, "batch_restore", rid, "not_found",
                                "batch aborted, nothing applied")
                    raise NotFoundError(f"resource {rid} not found; batch aborted")
                requested.append(r)
            requested.sort(key=self._depth)

            # 模拟恢复集合：校验每个请求项时把同批已确认的项视为已恢复
            planned: dict[str, Resource] = {}
            for r in requested:
                if r.id in planned:
                    continue  # 已被同批上游级联覆盖
                if not r.deleted:
                    self._audit(caller, "batch_restore", r.id, "conflict",
                                "not deleted; batch aborted")
                    raise ConflictError(f"resource {r.id} is not deleted; batch aborted")
                if not caller.can_operate(r.owner_id):
                    self._audit(caller, "batch_restore", r.id, "denied",
                                "batch aborted, nothing applied")
                    raise ForbiddenError(f"not allowed to restore {r.id}; batch aborted")
                self._check_retention(r)
                members = self._restore_closure(r)
                member_ids = {m.id for m in members}
                for m in members:
                    if m.parent_id is not None \
                            and m.parent_id not in member_ids \
                            and m.parent_id not in planned:
                        parent = self._store.resources.get(m.parent_id)
                        if parent is None or parent.deleted:
                            self._audit(caller, "batch_restore", m.id, "conflict",
                                        "parent deleted; batch aborted")
                            raise ConflictError(
                                f"parent of {m.id} is deleted; batch aborted")
                    self._ensure_name_free(
                        m.parent_id, m.name,
                        exclude_ids=member_ids | set(planned),
                        audit_id=m.id)
                planned.update({m.id: m for m in members})

            for m in planned.values():
                self._apply_restore(m)
            self._audit(caller, "batch_restore", None, "success",
                        f"restored: {sorted(planned)}")
            self._store.snapshot()
            return list(planned.values())

    # ================= 保留期回收 =================

    def recycle_expired(self, now: float | None = None) -> list[str]:
        """物理清除保留期到期的已删除资源。幂等、可中断重入：
        崩溃后重跑只会看到同样的到期集合并得到同样结果。"""
        now = self._now() if now is None else now
        with self._store.locked():
            expired = [
                r.id for r in self._store.resources.values()
                if r.deleted
                and r.deleted_at is not None
                and r.deleted_at + self._settings.retention_seconds <= now
            ]
            for rid in expired:
                del self._store.resources[rid]
                self._store.add_audit(SYSTEM_ACTOR, "recycle", rid,
                                      "success", "retention expired", now)
            if expired:
                self._store.snapshot()
            return sorted(expired)

    # ================= 内部工具 =================

    def _children_of(self, resource_id: str) -> list[Resource]:
        return [r for r in self._store.resources.values() if r.parent_id == resource_id]

    def _depth(self, r: Resource) -> int:
        depth, cur = 0, r
        while cur.parent_id is not None:
            parent = self._store.resources.get(cur.parent_id)
            if parent is None:
                break
            cur = parent
            depth += 1
        return depth

    def _ensure_name_free(self, parent_id: str | None, name: str,
                          exclude_ids: set[str] | None = None,
                          audit_id: str | None = None) -> None:
        """唯一性只在存活资源间生效：删除后名称即释放，恢复时重新校验。"""
        for r in self._store.resources.values():
            if r.deleted or r.parent_id != parent_id or r.name != name:
                continue
            if exclude_ids and r.id in exclude_ids:
                continue
            if audit_id:
                self._audit(SYSTEM_ACTOR, "restore", audit_id, "conflict",
                            f"name '{name}' already taken by {r.id}")
            raise ConflictError(
                f"name '{name}' already exists under parent {parent_id}")

    def _check_permission(self, caller: Caller, r: Resource, action: str) -> None:
        if not caller.can_operate(r.owner_id):
            self._audit(caller, action, r.id, "denied",
                        f"owner={r.owner_id}")
            raise ForbiddenError(f"caller {caller.user_id} cannot {action} {r.id}")

    def _check_retention(self, r: Resource) -> None:
        assert r.deleted_at is not None
        if self._now() > r.deleted_at + self._settings.retention_seconds:
            self._audit(SYSTEM_ACTOR, "restore", r.id, "expired",
                        "retention period elapsed")
            raise GoneError(f"retention period for {r.id} has expired")

    def _audit(self, caller: Caller, action: str, resource_id: str | None,
               result: str, detail: str) -> None:
        actor = caller.user_id if isinstance(caller, Caller) else caller
        self._store.add_audit(actor, action, resource_id, result, detail, self._now())
