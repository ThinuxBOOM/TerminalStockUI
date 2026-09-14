"""Opt-in API-key auth for production (stdlib only, no new deps).

Default (``API_KEY`` unset/empty): the API stays open — local dev, tests,
and the current frontend keep working with zero changes.

Production (``API_KEY`` set to a long random value, comma-separated list
supported for rotation): every ``/api/*`` request except ``/health`` must
send either::

    Authorization: Bearer <key>
    X-API-Key: <key>

Keys compare with :func:`hmac.compare_digest`. Failures are 401 with no
key-material echo. ``CRON_SECRET`` (backend/api/cron.py) is orthogonal and
unchanged.
"""

from __future__ import annotations

import hmac
import os

from fastapi import HTTPException, Request

_HEADER_CANDIDATES = ("x-api-key", "authorization")

# Paths that never require a key (liveness + framework introspection).
_OPEN_PATHS = ("/health", "/", "/openapi.json", "/docs", "/redoc")


def _configured_keys() -> list[str]:
    raw = os.getenv("API_KEY", "") or ""
    keys = [part.strip() for part in raw.split(",")]
    return [key for key in keys if key]


def auth_enabled() -> bool:
    """True when API_KEY enforcement is active."""
    return bool(_configured_keys())


def is_open_path(path: str) -> bool:
    text = (path or "/").split("?", 1)[0]
    if text in _OPEN_PATHS:
        return True
    if text.startswith("/docs") or text.startswith("/redoc") or text.startswith("/openapi"):
        return True
    return False


def _provided_key(request: Request) -> str:
    try:
        headers = request.headers or {}
    except Exception:
        return ""
    direct = headers.get("x-api-key", "") or ""
    if direct and direct.strip():
        return direct.strip()
    auth = headers.get("authorization", "") or ""
    if auth[:7].lower() == "bearer ":
        return auth[7:].strip()
    return ""


def check_api_key(request: Request) -> None:
    """Enforce the API key iff configured; no-op in dev. Raises 401."""
    keys = _configured_keys()
    if not keys:
        return
    try:
        path = request.url.path if request.url is not None else "/"
    except Exception:
        path = "/"
    if is_open_path(path):
        return
    # Only /api/* is guarded; unknown non-API paths fall through to 404.
    if not path.startswith("/api"):
        return
    provided = _provided_key(request)
    if not provided:
        raise HTTPException(status_code=401, detail="unauthorized")
    for key in keys:
        try:
            if hmac.compare_digest(provided, key):
                return
        except Exception:
            continue
    raise HTTPException(status_code=401, detail="unauthorized")


async def api_key_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
    """Pure-ASGI/HTTP middleware hook for FastAPI (BaseHTTPMiddleware style)."""
    check_api_key(request)
    return await call_next(request)
