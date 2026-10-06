"""调用方身份与权限。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Caller:
    """调用方身份。role=admin 可越权操作他人资源。"""

    user_id: str
    role: str = "user"

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def can_operate(self, owner_id: str) -> bool:
        return self.is_admin or self.user_id == owner_id
