"""V2 auth guards: JWT Bearer (HS256) + live-row user load + tier/admin gates.

- Tokens are HS256 with the existing ``SECRET_KEY`` (15min access,
  7-day refresh). ``PyJWT`` is used when installed; otherwise a minimal
  stdlib HS256 implementation handles the same ``{sub, tier, is_admin,
  type, iat, exp}`` claim shape (interoperable both ways).
- :func:`get_current_user` decodes the Bearer token, then loads the LIVE
  ``users`` row (tier/subscription fresh, never JWT-cached); 401 on
  missing/malformed/expired token or unknown user.
- :func:`require_tier` gates on live tier rank (reuses ``_TIER_RANK`` from
  ``tiers.py``): 401 unauthenticated, **402** when the rank is insufficient
  (body carries ``upgrade_required=True`` + ``min_tier``). ``is_admin``
  bypasses every tier gate. The legacy ``X-Tier`` header is never read.
- :func:`require_admin` gates on live ``is_admin``: 401 unauthenticated,
  403 otherwise.
- The ``User`` ORM class is imported from ``backend.db.models`` when the
  parallel Phase-1 change has landed; otherwise a compatible fallback
  mapping for the ``users`` table (same columns per ``docs/V2_PLAN.md``)
  is used, so auth works before/after the models land. All attribute
  access is ``getattr``-defensive.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from backend.auth.tiers import _TIER_RANK, normalize_tier
from backend.db.session import get_db

try:  # preferred JWT backend (V2 plan: pyjwt>=2.8)
    import jwt as _pyjwt
except Exception:  # pragma: no cover - exercised when PyJWT is absent
    _pyjwt = None  # type: ignore[assignment]

ACCESS_TOKEN_TTL_S = 15 * 60
REFRESH_TOKEN_TTL_S = 7 * 24 * 3600

_UNAUTHORIZED = "unauthorized"

_FallbackUser: Any = None


# --- JWT secret ---------------------------------------------------------------


def get_jwt_secret() -> str:
    """HS256 key: ``SECRET_KEY`` env, test fallback outside production."""
    try:
        raw = (os.getenv("SECRET_KEY", "") or "").strip()
    except Exception:
        raw = ""
    if raw:
        return raw
    return "test-only-secret-key-for-unit-tests-123"


# --- stdlib HS256 (used only when PyJWT is unavailable) ------------------------


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    try:
        padded = str(data or "") + "=" * (-len(str(data or "")) % 4)
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except Exception as exc:
        raise _TokenError("malformed token") from exc


class _TokenError(ValueError):
    """Any token problem (malformed, bad signature, expired, wrong type)."""


def _stdlib_encode(payload: dict) -> str:
    header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64url_encode(json.dumps(payload, separators=(",", ":"), default=str).encode())
    signing_input = f"{header}.{body}".encode("ascii")
    sig = hmac.new(get_jwt_secret().encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header}.{body}.{_b64url_encode(sig)}"


def _stdlib_decode(token: str) -> dict:
    try:
        header_b64, body_b64, sig_b64 = str(token or "").split(".")
        signing_input = f"{header_b64}.{body_b64}".encode("ascii")
        expected = hmac.new(get_jwt_secret().encode("utf-8"), signing_input, hashlib.sha256).digest()
        try:
            ok = hmac.compare_digest(_b64url_decode(sig_b64), expected)
        except Exception:
            ok = False
        if not ok:
            raise _TokenError("bad signature")
        header = json.loads(_b64url_decode(header_b64).decode("utf-8"))
        if str(header.get("alg", "")).upper() != "HS256":
            raise _TokenError("unexpected algorithm")
        payload = json.loads(_b64url_decode(body_b64).decode("utf-8"))
    except _TokenError:
        raise
    except Exception as exc:
        raise _TokenError("malformed token") from exc
    if not isinstance(payload, dict):
        raise _TokenError("malformed token")
    try:
        exp = float(payload.get("exp", 0))
    except (TypeError, ValueError) as exc:
        raise _TokenError("malformed token") from exc
    if exp <= time.time():
        raise _TokenError("expired token")
    return payload


# --- token issue / verify (PyJWT when present, stdlib otherwise) ---------------


def _claims(user_id: str, token_type: str, tier: str, is_admin: bool, ttl_s: int) -> dict:
    now = int(time.time())
    return {
        "sub": str(user_id),
        "tier": str(tier or "free"),
        "is_admin": bool(is_admin),
        "type": str(token_type),
        "iat": now,
        "exp": now + int(ttl_s),
    }


def create_access_token(
    user_id: str,
    tier: str = "free",
    is_admin: bool = False,
    expires_in_s: int = ACCESS_TOKEN_TTL_S,
) -> str:
    """Mint a short-lived access token (tier/is_admin are informational)."""
    payload = _claims(user_id, "access", tier, is_admin, expires_in_s)
    if _pyjwt is not None:
        return _pyjwt.encode(payload, get_jwt_secret(), algorithm="HS256")
    return _stdlib_encode(payload)


def create_refresh_token(
    user_id: str,
    expires_in_s: int = REFRESH_TOKEN_TTL_S,
) -> str:
    """Mint a refresh token (identity only; tier reloaded live on use)."""
    payload = _claims(user_id, "refresh", "free", False, expires_in_s)
    if _pyjwt is not None:
        return _pyjwt.encode(payload, get_jwt_secret(), algorithm="HS256")
    return _stdlib_encode(payload)


def decode_token(token: str, *, expected_type: str = "access") -> dict:
    """Verify signature + expiry (+ ``type``); raises :class:`_TokenError`."""
    text = str(token or "").strip()
    if not text:
        raise _TokenError("missing token")
    if _pyjwt is not None:
        try:
            payload = _pyjwt.decode(text, get_jwt_secret(), algorithms=["HS256"])
        except _pyjwt.ExpiredSignatureError as exc:  # type: ignore[union-attr]
            raise _TokenError("expired token") from exc
        except Exception as exc:
            raise _TokenError("invalid token") from exc
        if not isinstance(payload, dict):
            raise _TokenError("malformed token")
    else:
        payload = _stdlib_decode(text)
    if str(payload.get("type", "")) != str(expected_type):
        raise _TokenError("wrong token type")
    if not str(payload.get("sub", "") or "").strip():
        raise _TokenError("missing subject")
    return payload


# --- User model (parallel Phase-1 change may not have landed yet) --------------


def get_user_model() -> Any:
    """Return the ``User`` ORM class, with a compatible fallback mapping.

    Prefers ``backend.db.models.User`` (Phase 1: ``id/email/password_hash/
    tier/stripe_customer_id/stripe_subscription_id/subscription_status/
    is_admin``). Falls back to an ``extend_existing`` mapping of the same
    ``users`` table so auth works before that change lands.
    """
    try:
        from backend.db.models import User as Imported  # type: ignore

        return Imported
    except Exception:
        pass
    global _FallbackUser
    if _FallbackUser is not None:
        return _FallbackUser
    import sqlalchemy as _sa
    from sqlalchemy.orm import Mapped as _Mapped
    from sqlalchemy.orm import mapped_column as _mc

    from backend.db.models import ID_TYPE, Base

    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)

    class FallbackUser(Base):  # type: ignore[no-redef]
        __tablename__ = "users"
        __table_args__ = {"extend_existing": True}

        id: _Mapped[Any] = _mc(ID_TYPE, primary_key=True, default=uuid.uuid4)
        email: _Mapped[str] = _mc(_sa.Text, nullable=False, unique=True)
        password_hash: _Mapped[str] = _mc(_sa.Text, nullable=False)
        tier: _Mapped[str] = _mc(_sa.String(16), nullable=False, default="free")
        stripe_customer_id: _Mapped[Any] = _mc(_sa.Text, nullable=True, unique=True, default=None)
        stripe_subscription_id: _Mapped[Any] = _mc(_sa.Text, nullable=True, default=None)
        subscription_status: _Mapped[Any] = _mc(_sa.Text, nullable=True, default=None)
        is_admin: _Mapped[bool] = _mc(_sa.Boolean, nullable=False, default=False)
        created_at: _Mapped[Any] = _mc(_sa.DateTime(timezone=True), default=_utcnow)
        updated_at: _Mapped[Any] = _mc(_sa.DateTime(timezone=True), default=_utcnow)

    _FallbackUser = FallbackUser
    return _FallbackUser


def _load_user(db: Session, user_id: str) -> Any | None:
    User = get_user_model()
    try:
        key: Any = uuid.UUID(str(user_id))
    except Exception:
        return None
    try:
        from sqlalchemy import select as _select

        row = db.execute(_select(User).where(User.id == key)).scalar_one_or_none()
        return row
    except Exception:
        return None


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=401, detail=_UNAUTHORIZED)


def _bearer_token(request: Request) -> str:
    try:
        auth = (request.headers.get("authorization", "") or "").strip()
    except Exception:
        return ""
    if len(auth) < 8 or auth[:7].lower() != "bearer ":
        return ""
    return auth[7:].strip()


# --- dependencies --------------------------------------------------------------


def get_current_user(request: Request, db: Session = Depends(get_db)) -> Any:
    """Live-row authed user (401 on missing/expired/unknown token/user)."""
    token = _bearer_token(request)
    if not token:
        raise _unauthorized()
    try:
        payload = decode_token(token, expected_type="access")
    except _TokenError:
        raise _unauthorized()
    except Exception:
        raise _unauthorized()
    try:
        user = _load_user(db, str(payload.get("sub", "")))
    except Exception:
        user = None
    if user is None:
        raise _unauthorized()
    return user


def require_tier(min_tier: str) -> Callable:
    """Dependency factory gating on the LIVE tier rank (402 when too low).

    ``is_admin`` bypasses. Raises ``ValueError`` at wiring time for an
    unknown ``min_tier`` (fail-closed: misconfigured gates never open).
    """
    need = str(min_tier or "").strip().lower()
    if need not in _TIER_RANK:
        raise ValueError(f"unknown min_tier: {min_tier!r}")

    def _gate(user: Any = Depends(get_current_user)) -> Any:
        try:
            if bool(getattr(user, "is_admin", False)):
                return user
        except Exception:
            pass
        try:
            tier = normalize_tier(getattr(user, "tier", "free"))
        except Exception:
            tier = "free"
        if _TIER_RANK.get(tier, 0) < _TIER_RANK[need]:
            raise HTTPException(
                status_code=402,
                detail={
                    "message": f"tier '{tier}' insufficient; requires '{need}' or higher",
                    "upgrade_required": True,
                    "min_tier": need,
                    "tier": tier,
                },
            )
        return user

    return _gate


def require_admin(user: Any = Depends(get_current_user)) -> Any:
    """Dependency gating on live ``is_admin`` (401 unauth, 403 non-admin)."""
    try:
        if bool(getattr(user, "is_admin", False)):
            return user
    except Exception:
        pass
    raise HTTPException(status_code=403, detail="admin access required")


__all__ = [
    "ACCESS_TOKEN_TTL_S",
    "REFRESH_TOKEN_TTL_S",
    "create_access_token",
    "create_refresh_token",
    "decode_token",
    "get_current_user",
    "get_jwt_secret",
    "get_user_model",
    "require_admin",
    "require_tier",
]
