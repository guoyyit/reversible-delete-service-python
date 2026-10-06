"""领域模型：资源与审计记录。"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field


class DeleteOrigin(str, enum.Enum):
    """删除来源：直接删除 / 级联删除。"""

    DIRECT = "direct"
    CASCADE = "cascade"


@dataclass
class Resource:
    """资源实体。删除是可逆的软删除。"""

    id: str
    name: str
    owner_id: str
    parent_id: str | None = None
    deleted: bool = False
    deleted_at: float | None = None
    deleted_by: str | None = None
    delete_origin: DeleteOrigin | None = None
    # 级联删除时指向本次级联的根资源 id，用于恢复时精确还原
    cascade_root_id: str | None = None
    version: int = 0
    created_at: float = 0.0


@dataclass
class AuditRecord:
    """审计记录：谁在何时对何资源做了什么，结果如何。"""

    id: str
    actor_id: str
    action: str  # delete / restore / recycle / batch_delete / batch_restore
    resource_id: str | None
    result: str  # success / denied / conflict / not_found / expired
    detail: str = ""
    timestamp: float = 0.0
