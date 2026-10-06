"""状态存储：内存数据 + 原子快照持久化。所有访问在单把可重入锁下完成。"""
from __future__ import annotations

import threading

from app.models import AuditRecord, Resource


class StateStore:
    def __init__(self, state_file: str = ""):
        self._lock = threading.RLock()
        self._resources: dict[str, Resource] = {}
        self._audit: list[AuditRecord] = []
        self._state_file = state_file

    # ---- 持久化（骨架，后续填充） ----
    def load(self) -> None:
        """启动时加载快照并返回；文件不存在时视为空状态。"""
        raise NotImplementedError

    def snapshot(self) -> None:
        """将当前状态原子写入快照文件。"""
        raise NotImplementedError
