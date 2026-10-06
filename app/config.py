"""Runtime configuration (retention period, admins, storage path)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

DEFAULT_RETENTION_SECONDS = 30 * 24 * 3600  # 30 days


@dataclass(frozen=True)
class Settings:
    db_path: str = ":memory:"
    retention_seconds: int = DEFAULT_RETENTION_SECONDS
    admin_users: frozenset[str] = field(default_factory=frozenset)
    recycle_batch_size: int = 100

    @staticmethod
    def from_env() -> "Settings":
        admins = {
            u.strip()
            for u in os.environ.get("RDS_ADMIN_USERS", "").split(",")
            if u.strip()
        }
        return Settings(
            db_path=os.environ.get("RDS_DB_PATH", ":memory:"),
            retention_seconds=int(
                os.environ.get("RDS_RETENTION_SECONDS", DEFAULT_RETENTION_SECONDS)
            ),
            admin_users=frozenset(admins),
            recycle_batch_size=int(os.environ.get("RDS_RECYCLE_BATCH_SIZE", "100")),
        )
