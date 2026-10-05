"""HTTP hardening middlewares.

- :class:`SecurityHeadersMiddleware`: strict headers on every API response
  (HSTS in production).
- :class:`RequestSizeLimitMiddleware`: 413 when ``Content-Length`` exceeds
  ``MAX_REQUEST_BYTES`` (default 1 MB).
- :func:`rate_limit_middleware`: per-client sliding-window 429s (see
  rate_limit.py).
"""

from __future__ import annotations

import os

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from backend import settings


def _max_request_bytes() -> int:
    try:
        value = int((os.getenv("MAX_REQUEST_BYTES", "1000000") or "1000000").strip())
    except ValueError:
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
        # The API only ever returns JSON: nothing may execute or be framed.
        headers.setdefault(
            "Content-Security-Policy",
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
        )
        if settings.is_production():
            headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        limit = _max_request_bytes()
        raw = (request.headers.get("content-length") or "").strip()
        if raw:
            try:
                size = int(raw)
            except ValueError:
                return JSONResponse(status_code=400, content={"detail": "invalid content-length"})
            if size > limit:
                return JSONResponse(
                    status_code=413,
                    content={"detail": f"request body too large (limit {limit} bytes)"},
                )
        return await call_next(request)


async def rate_limit_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
    from backend.security.rate_limit import check_rate_limit

    try:
        quota = check_rate_limit(request)
    except HTTPException as exc:
        return JSONResponse(
            status_code=exc.status_code, content={"detail": exc.detail}, headers=dict(exc.headers or {}),
        )
    response = await call_next(request)
    if quota is not None:
        limit, remaining = quota
        response.headers.setdefault("X-RateLimit-Limit", str(limit))
        response.headers.setdefault("X-RateLimit-Remaining", str(remaining))
    return response
