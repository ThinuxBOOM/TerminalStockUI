"""V2 auth routes: register / login / me / refresh (JWT HS256 + bcrypt).

- ``POST /api/auth/register`` — validate email (email-validator when
  installed, regex fallback) + ``lower(trim(email))`` + password >= 10,
  bcrypt-hash, ``INSERT tier='free'``; 201 + access token (15min) + refresh
  in an httpOnly cookie (``Secure`` on https/prod). Duplicate -> 409.
- ``POST /api/auth/login`` — same 401 message on miss/mismatch (no user
  enumeration); rotates the refresh cookie.
- ``GET /api/auth/me`` — live ``users`` row (tier/subscription fresh, not
  JWT-cached); password hash never serialized.
- ``POST /api/auth/refresh`` — refresh-cookie rotation -> new access token.
"""

from __future__ import annotations

import os
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.auth.guards import (
    ACCESS_TOKEN_TTL_S,
    REFRESH_TOKEN_TTL_S,
    _TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_user,
    get_user_model,
)
from backend.auth.tiers import normalize_tier
from backend.db.session import get_db
from backend.security.passwords import hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])

REFRESH_COOKIE = "refresh_token"
_INVALID_CREDENTIALS = "invalid email or password"
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class Credentials(BaseModel):
    email: str
    password: str


def _normalize_email(raw: object) -> str:
    """Lower(trim(email)); raises 422 when invalid.

    Uses email-validator when installed (no deliverability probe — offline
    safe), otherwise a conservative regex fallback.
    """
    text = str(raw or "").strip().lower()
    if not text or len(text) > 320:
        raise HTTPException(status_code=422, detail="invalid email")
    try:
        from email_validator import EmailNotValidError, validate_email

        try:
            info = validate_email(text, check_deliverability=False)
        except EmailNotValidError:
            raise HTTPException(status_code=422, detail="invalid email")
        normalized = str(getattr(info, "normalized", "") or getattr(info, "email", "") or text)
        normalized = normalized.strip().lower()
        if not normalized or not _EMAIL_RE.match(normalized):
            raise HTTPException(status_code=422, detail="invalid email")
        return normalized
    except ImportError:
        if not _EMAIL_RE.match(text):
            raise HTTPException(status_code=422, detail="invalid email")
        return text


def _check_password_length(password: object) -> str:
    if not isinstance(password, str) or len(password) < 10:
        raise HTTPException(status_code=422, detail="password must be at least 10 characters")
    return password


def _public_user(user: object) -> dict:
    try:
        tier = normalize_tier(getattr(user, "tier", "free"))
    except Exception:
        tier = "free"
    return {
        "id": str(getattr(user, "id", "")),
        "email": getattr(user, "email", ""),
        "tier": tier,
        "subscription_status": getattr(user, "subscription_status", None),
        "is_admin": bool(getattr(user, "is_admin", False)),
    }


def _is_secure_request(request: Request) -> bool:
    try:
        if str(getattr(request.url, "scheme", "") or "").lower() == "https":
            return True
    except Exception:
        pass
    try:
        return (os.getenv("APP_ENV", "") or "").strip().lower() in ("production", "prod")
    except Exception:
        return False


def _auth_response(user: object, request: Request, *, status_code: int) -> JSONResponse:
    access = create_access_token(
        str(getattr(user, "id", "")),
        tier=str(getattr(user, "tier", "free") or "free"),
        is_admin=bool(getattr(user, "is_admin", False)),
        expires_in_s=ACCESS_TOKEN_TTL_S,
    )
    refresh = create_refresh_token(str(getattr(user, "id", "")))
    body = _public_user(user)
    body.update({"access_token": access, "token_type": "bearer"})
    response = JSONResponse(status_code=status_code, content=body)
    response.set_cookie(
        REFRESH_COOKIE,
        refresh,
        max_age=REFRESH_TOKEN_TTL_S,
        expires=REFRESH_TOKEN_TTL_S,
        path="/",
        httponly=True,
        secure=_is_secure_request(request),
        samesite="lax",
    )
    return response


def _find_by_email(db: Session, email: str) -> object | None:
    User = get_user_model()
    try:
        from sqlalchemy import select as _select

        return db.execute(_select(User).where(User.email == email)).scalar_one_or_none()
    except Exception:
        return None


@router.post("/register", status_code=201)
def register(body: Credentials, request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    email = _normalize_email(body.email)
    _check_password_length(body.password)
    if _find_by_email(db, email) is not None:
        raise HTTPException(status_code=409, detail="email already registered")
    User = get_user_model()
    try:
        password_hash = hash_password(body.password)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    user = User(email=email, password_hash=password_hash, tier="free")
    try:
        is_admin = getattr(user, "is_admin", None)
        if is_admin is None:
            user.is_admin = False
    except Exception:
        pass
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="email already registered")
    except Exception:
        db.rollback()
        raise
    try:
        db.refresh(user)
    except Exception:
        pass
    return _auth_response(user, request, status_code=201)


@router.post("/login")
def login(body: Credentials, request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    # No enumeration: every failure below returns the same 401 message
    # (including malformed emails and hash errors).
    try:
        email = _normalize_email(body.email)
    except HTTPException:
        raise HTTPException(status_code=401, detail=_INVALID_CREDENTIALS)
    password = body.password if isinstance(body.password, str) else ""
    user = _find_by_email(db, email)
    if user is None:
        raise HTTPException(status_code=401, detail=_INVALID_CREDENTIALS)
    try:
        ok = verify_password(password, getattr(user, "password_hash", None))
    except Exception:
        ok = False
    if not ok:
        raise HTTPException(status_code=401, detail=_INVALID_CREDENTIALS)
    return _auth_response(user, request, status_code=200)


@router.get("/me")
def me(user: object = Depends(get_current_user)) -> dict:
    return _public_user(user)


@router.post("/refresh")
def refresh(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    try:
        token = (request.cookies.get(REFRESH_COOKIE, "") or "").strip()
    except Exception:
        token = ""
    if not token:
        raise HTTPException(status_code=401, detail="unauthorized")
    try:
        payload = decode_token(token, expected_type="refresh")
    except _TokenError:
        raise HTTPException(status_code=401, detail="unauthorized")
    except Exception:
        raise HTTPException(status_code=401, detail="unauthorized")
    User = get_user_model()
    user: object | None = None
    try:
        from sqlalchemy import select as _select

        import uuid as _uuid

        try:
            key = _uuid.UUID(str(payload.get("sub", "")))
        except Exception:
            key = None
        if key is not None:
            user = db.execute(_select(User).where(User.id == key)).scalar_one_or_none()
    except Exception:
        user = None
    if user is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    return _auth_response(user, request, status_code=200)


__all__ = ["login", "me", "refresh", "register", "router"]
