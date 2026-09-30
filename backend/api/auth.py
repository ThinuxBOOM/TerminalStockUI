"""Auth routes: register / login / me / refresh / logout.

- ``POST /api/auth/register`` — normalized email + password >= 10 chars,
  bcrypt hash; 201 + access token + httpOnly refresh cookie. Registration can
  be closed with ``ALLOW_REGISTRATION=false`` (default in production: closed
  unless explicitly enabled — create users with ``scripts/create_user.py``).
- ``POST /api/auth/login`` — one 401 message for every failure (no user
  enumeration; a dummy bcrypt check keeps unknown-email timing comparable).
- ``GET /api/auth/me`` — the live users row (password hash never serialized).
- ``POST /api/auth/refresh`` — refresh cookie -> new access token + rotated
  cookie. Revoked (logged-out) refresh tokens are rejected.
- ``POST /api/auth/logout`` — bumps ``token_version`` (revokes every token for
  the user) and clears the refresh cookie.
"""

from __future__ import annotations

import os

from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend import settings
from backend.auth.guards import (
    ACCESS_TOKEN_TTL_S,
    REFRESH_TOKEN_TTL_S,
    create_access_token,
    create_refresh_token,
    get_current_user,
    user_for_token,
)
from backend.db.models import User
from backend.db.session import get_db
from backend.security.passwords import hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])

REFRESH_COOKIE = "refresh_token"
REFRESH_COOKIE_PATH = "/api/auth"
MIN_PASSWORD_LEN = 10
_INVALID_CREDENTIALS = "invalid email or password"
# Verified against when the email is unknown so both paths cost one bcrypt check.
_DUMMY_HASH = hash_password("timing-equalizer-not-a-real-password")


class Credentials(BaseModel):
    email: str
    password: str


def registration_open() -> bool:
    default = "false" if settings.is_production() else "true"
    raw = (os.getenv("ALLOW_REGISTRATION", default) or default).strip().lower()
    return raw in ("1", "true", "yes", "on")


def normalize_email(raw: str) -> str:
    text = (raw or "").strip().lower()
    if not text or len(text) > 320:
        raise HTTPException(status_code=422, detail="invalid email")
    try:
        info = validate_email(text, check_deliverability=False)
    except EmailNotValidError:
        raise HTTPException(status_code=422, detail="invalid email") from None
    return info.normalized.strip().lower()


def public_user(user: User) -> dict:
    return {"id": str(user.id), "email": user.email, "is_admin": bool(user.is_admin)}


def _auth_response(user: User, request: Request, *, status_code: int) -> JSONResponse:
    body = public_user(user)
    body.update({
        "access_token": create_access_token(user),
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_TTL_S,
    })
    response = JSONResponse(status_code=status_code, content=body)
    response.set_cookie(
        REFRESH_COOKIE,
        create_refresh_token(user),
        max_age=REFRESH_TOKEN_TTL_S,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=settings.is_production() or request.url.scheme == "https",
        samesite="strict",
    )
    return response


def _find_by_email(db: Session, email: str) -> User | None:
    return db.execute(select(User).where(User.email == email)).scalar_one_or_none()


@router.post("/register", status_code=201)
def register(body: Credentials, request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    if not registration_open():
        raise HTTPException(status_code=403, detail="registration is closed")
    email = normalize_email(body.email)
    if len(body.password) < MIN_PASSWORD_LEN:
        raise HTTPException(
            status_code=422, detail=f"password must be at least {MIN_PASSWORD_LEN} characters",
        )
    try:
        password_hash = hash_password(body.password)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    user = User(email=email, password_hash=password_hash, is_admin=False, token_version=0)
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="email already registered") from None
    db.refresh(user)
    return _auth_response(user, request, status_code=201)


@router.post("/login")
def login(body: Credentials, request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    try:
        email = normalize_email(body.email)
    except HTTPException:
        raise HTTPException(status_code=401, detail=_INVALID_CREDENTIALS) from None
    user = _find_by_email(db, email)
    if user is None:
        verify_password(body.password, _DUMMY_HASH)
        raise HTTPException(status_code=401, detail=_INVALID_CREDENTIALS)
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail=_INVALID_CREDENTIALS)
    return _auth_response(user, request, status_code=200)


@router.get("/me")
def me(user: User = Depends(get_current_user)) -> dict:
    return public_user(user)


@router.post("/refresh")
def refresh(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    token = (request.cookies.get(REFRESH_COOKIE) or "").strip()
    user = user_for_token(db, token, expected_type="refresh") if token else None
    if user is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    return _auth_response(user, request, status_code=200)


@router.post("/logout", status_code=204)
def logout(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Response:
    user.token_version = int(user.token_version or 0) + 1
    db.commit()
    response = Response(status_code=204)
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_COOKIE_PATH)
    return response


__all__ = ["router", "registration_open", "normalize_email", "public_user"]
