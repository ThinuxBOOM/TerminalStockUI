"""Authorization stub: role model + permission matrix + FastAPI dependency.

v1 roles: admin > analyst > viewer; system for workers. Full policy engine
later; this stub enforces the same dependency interface.
"""

from __future__ import annotations

from enum import Enum

from fastapi import Header, HTTPException


class Role(str, Enum):
    ADMIN = "admin"
    ANALYST = "analyst"
    VIEWER = "viewer"
    SYSTEM = "system"


ROLE_PERMISSIONS: dict[Role, set[str]] = {
    Role.ADMIN: {"read", "research", "configure_providers", "manage_users"},
    Role.ANALYST: {"read", "research"},
    Role.VIEWER: {"read"},
    Role.SYSTEM: {"read", "research", "ingest"},
}


def check_permission(role: Role | str, action: str) -> bool:
    if isinstance(role, Role):
        resolved = role
    else:
        try:
            resolved = Role(str(role).lower())
        except ValueError:
            return False
    return action in ROLE_PERMISSIONS.get(resolved, set())


def require_permission(action: str):
    """FastAPI dependency factory enforcing an action on X-Role header."""

    async def _dep(x_role: str = Header(default="viewer")) -> Role:
        if not check_permission(x_role, action):
            raise HTTPException(status_code=403, detail=f"role {x_role!r} lacks {action!r}")
        return Role(str(x_role).lower())

    return _dep
