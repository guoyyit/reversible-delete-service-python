"""配置项。"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Settings:
    # 保留期（秒），默认 7 天；保留期内可恢复，到期后由回收器物理清除
    retention_seconds: float = 7 * 24 * 3600
    # 状态快照文件路径；空字符串表示纯内存（不持久化）
    state_file: str = ""
    # 后台回收间隔（秒）；<=0 表示不启动后台回收线程
    recycle_interval_seconds: float = 60.0

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            retention_seconds=float(os.getenv("RETENTION_SECONDS", str(7 * 24 * 3600))),
            state_file=os.getenv("STATE_FILE", ""),
            recycle_interval_seconds=float(os.getenv("RECYCLE_INTERVAL_SECONDS", "60")),
        )
