"""Caller identity and ownership checks."""

from __future__ import annotations

from dataclasses import dataclass

from .config import Settings
from .errors import ForbiddenError, UnauthorizedError


@dataclass(frozen=True)
class Caller:
    user_id: str
    is_admin: bool

    def require_owner_or_admin(self, owner_id: str) -> None:
        if not self.is_admin and self.user_id != owner_id:
            raise ForbiddenError(
                f"user {self.user_id!r} cannot act on resource of {owner_id!r}"
            )


def resolve_caller(user_id: str | None, settings: Settings) -> Caller:
    if not user_id:
        raise UnauthorizedError("missing X-User-Id header")
    return Caller(user_id=user_id, is_admin=user_id in settings.admin_users)
