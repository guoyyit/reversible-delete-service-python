"""Data models for resources and audit entries."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Resource:
    id: str
    name: str
    parent_id: str | None
    owner_id: str
    content: str | None
    created_at: str
    updated_at: str
    deleted_at: str | None
    delete_root_id: str | None
    deleted_by: str | None
    delete_reason: str | None


@dataclass(frozen=True)
class AuditEntry:
    id: int
    ts: str
    actor: str
    action: str
    resource_id: str | None
    detail: str | None
