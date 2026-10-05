"""Auth guards: HS256 JWT bearer tokens + live user rows.

- Access tokens live 15 minutes, refresh tokens 7 days. Both are signed with
  ``settings.secret_key()`` (which refuses weak/missing keys in production).
- :func:`get_current_user` verifies the bearer token and loads the LIVE
  ``users`` row, so admin changes and deletions apply immediately. Tokens
  carry the user's ``token_version``; bumping it revokes them all.
- :func:`require_admin` additionally requires ``users.is_admin``.

Every ``/api/*`` router except auth, health and cron depends on
:func:`get_current_user` (see ``backend/api/main.py``).
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import settings
from backend.db.models import User
from backend.db.session import get_db

ACCESS_TOKEN_TTL_S = 15 * 60
REFRESH_TOKEN_TTL_S = 7 * 24 * 3600
_ALGORITHM = "HS256"


class TokenError(ValueError):
    """Any token problem (malformed, bad signature, expired, wrong type)."""


def _encode(user: Any, token_type: str, ttl_s: int) -> str:
    now = int(time.time())
    payload = {
        "sub": user_id_of(user),
        "ver": int(getattr(user, "token_version", 0) or 0),
        "type": token_type,
        "iat": now,
        "exp": now + int(ttl_s),
    }
    return jwt.encode(payload, settings.secret_key(), algorithm=_ALGORITHM)


def create_access_token(user: Any, expires_in_s: int = ACCESS_TOKEN_TTL_S) -> str:
    return _encode(user, "access", expires_in_s)


def create_refresh_token(user: Any, expires_in_s: int = REFRESH_TOKEN_TTL_S) -> str:
    return _encode(user, "refresh", expires_in_s)


def user_for_token(db: Session, token: str, *, expected_type: str) -> User | None:
    """Verify ``token`` and return its live user, or None when revoked/unknown."""
    try:
        payload = decode_token(token, expected_type=expected_type)
    except TokenError:
        return None
    user = load_user(db, payload["sub"])
    if user is None or int(payload.get("ver", -1)) != int(user.token_version or 0):
        return None
    return user


def decode_token(token: str, *, expected_type: str = "access") -> dict:
    text = (token or "").strip()
    if not text:
        raise TokenError("missing token")
    try:
        payload = jwt.decode(
            text, settings.secret_key(), algorithms=[_ALGORITHM],
            options={"require": ["sub", "exp", "type"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("expired token") from exc
    except jwt.PyJWTError as exc:
        raise TokenError("invalid token") from exc
    if payload.get("type") != expected_type:
        raise TokenError("wrong token type")
    return payload


def load_user(db: Session, user_id: str) -> User | None:
    try:
        key = uuid.UUID(str(user_id))
    except ValueError:
        return None
    return db.execute(select(User).where(User.id == key)).scalar_one_or_none()


def _bearer_token(request: Request) -> str:
    auth = (request.headers.get("authorization") or "").strip()
    if auth[:7].lower() != "bearer ":
        return ""
    return auth[7:].strip()


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401, detail="unauthorized", headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """The authenticated user (401 on a missing/invalid/expired token or unknown user).

    Database errors propagate (500), they are never reported as 401.
    """
    token = _bearer_token(request)
    if not token:
        raise _unauthorized()
    user = user_for_token(db, token, expected_type="access")
    if user is None:
        raise _unauthorized()
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not bool(getattr(user, "is_admin", False)):
        raise HTTPException(status_code=403, detail="admin access required")
    return user


def user_id_of(user: Any) -> str:
    """String id of a User row (or the dict-shaped user used by test overrides)."""
    if isinstance(user, dict):
        return str(user.get("id") or user.get("user_id") or "")
    return str(getattr(user, "id", "") or "")


__all__ = [
    "ACCESS_TOKEN_TTL_S",
    "REFRESH_TOKEN_TTL_S",
    "TokenError",
    "create_access_token",
    "create_refresh_token",
    "decode_token",
    "get_current_user",
    "load_user",
    "require_admin",
    "user_for_token",
    "user_id_of",
]
