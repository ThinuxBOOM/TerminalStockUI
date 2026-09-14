"""HTTP hardening middlewares (stdlib only).

- :class:`SecurityHeadersMiddleware`: CSP + friends on every response.
- :class:`RequestSizeLimitMiddleware`: 413 when ``Content-Length`` exceeds
  ``MAX_REQUEST_BYTES`` (default 1 MiB). Guards /providers/keys, /alerts,
  /ai/* against oversized JSON bodies without reading the stream.
- :func:`rate_limit_middleware`: sliding-window 429s (see rate_limit.py).
- :func:`api_key_middleware`: opt-in Bearer auth (see auth.py).
"""

from __future__ import annotations

import os

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


def _max_request_bytes() -> int:
    try:
        value = int((os.getenv("MAX_REQUEST_BYTES", "1000000") or "1000000").strip())
    except (TypeError, ValueError):
        return 1_000_000
    return max(1024, min(value, 50_000_000))


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        # API + SPA: no inline scripts/styles needed from our own responses;
        # keep the policy tight but non-breaking (no script-src enforcement
        # that would block the Vite bundle — the bundle ships its own hashes
        # via the frontend server, this header only hardens API responses).
        headers.setdefault(
            "Content-Security-Policy",
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
        )
        if (os.getenv("APP_ENV", "") or "").strip().lower() == "production":
            headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        limit = _max_request_bytes()
        try:
            raw = request.headers.get("content-length", "") or ""
            if raw.strip():
                size = int(raw.strip())
                if size > limit:
                    return JSONResponse(
                        status_code=413,
                        content={"detail": f"request body too large (limit {limit} bytes)"},
                    )
        except (TypeError, ValueError):
            pass
        except Exception:
            pass
        return await call_next(request)


async def rate_limit_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
    from backend.security.rate_limit import check_rate_limit

    try:
        quota = check_rate_limit(request)
    except HTTPException as exc:
        headers = dict(exc.headers or {}) if getattr(exc, "headers", None) else {}
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=headers)
    response = await call_next(request)
    try:
        if quota is not None:
            limit, remaining = quota
            response.headers.setdefault("X-RateLimit-Limit", str(limit))
            response.headers.setdefault("X-RateLimit-Remaining", str(remaining))
    except Exception:
        pass
    return response


async def api_key_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
    from backend.security.auth import check_api_key

    try:
        check_api_key(request)
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return await call_next(request)
