"""状态存储：内存数据 + 原子快照持久化。

所有对 resources / audit 的读写都必须在 `store.locked()` 临界区内进行，
保证任一时刻对外可见的是单一一致状态（不存在字段级混合的半写状态）。
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from typing import Iterator

from app.models import AuditRecord, DeleteOrigin, Resource

SCHEMA_VERSION = 1


def _resource_to_dict(r: Resource) -> dict:
    d = asdict(r)
    d["delete_origin"] = r.delete_origin.value if r.delete_origin else None
    return d


def _resource_from_dict(d: dict) -> Resource:
    d = dict(d)
    d["delete_origin"] = DeleteOrigin(d["delete_origin"]) if d.get("delete_origin") else None
    return Resource(**d)


class StateStore:
    def __init__(self, state_file: str = ""):
        self._lock = threading.RLock()
        self._resources: dict[str, Resource] = {}
        self._audit: list[AuditRecord] = []
        self._state_file = state_file

    # ---- 并发控制 ----
    @contextmanager
    def locked(self) -> Iterator[None]:
        """临界区：读-改-写必须整体持有同一把可重入锁。"""
        with self._lock:
            yield

    # ---- 数据访问（调用方须已持锁） ----
    @property
    def resources(self) -> dict[str, Resource]:
        return self._resources

    @property
    def audit(self) -> list[AuditRecord]:
        return self._audit

    def add_audit(self, actor_id: str, action: str, resource_id: str | None,
                  result: str, detail: str, timestamp: float) -> AuditRecord:
        record = AuditRecord(
            id=uuid.uuid4().hex,
            actor_id=actor_id,
            action=action,
            resource_id=resource_id,
            result=result,
            detail=detail,
            timestamp=timestamp,
        )
        self._audit.append(record)
        return record

    # ---- 持久化 ----
    def load(self) -> None:
        """启动时加载快照；文件不存在视为空状态，文件损坏则报错（不静默丢数据）。"""
        if not self._state_file or not os.path.exists(self._state_file):
            return
        with self._lock:
            with open(self._state_file, "r", encoding="utf-8") as f:
                try:
                    payload = json.load(f)
                except json.JSONDecodeError as e:
                    raise RuntimeError(f"state file corrupted: {self._state_file}") from e
            if payload.get("schema_version") != SCHEMA_VERSION:
                raise RuntimeError(f"unsupported schema version: {payload.get('schema_version')}")
            self._resources = {
                r["id"]: _resource_from_dict(r) for r in payload.get("resources", [])
            }
            self._audit = [AuditRecord(**a) for a in payload.get("audit", [])]

    def snapshot(self) -> None:
        """将当前状态原子写入快照文件（临时文件 + rename，崩溃不会留下半截文件）。"""
        if not self._state_file:
            return
        with self._lock:
            payload = {
                "schema_version": SCHEMA_VERSION,
                "resources": [_resource_to_dict(r) for r in self._resources.values()],
                "audit": [asdict(a) for a in self._audit],
            }
            tmp = self._state_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._state_file)
