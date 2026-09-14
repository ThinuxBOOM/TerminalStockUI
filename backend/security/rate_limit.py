"""In-memory sliding-window rate limiter (stdlib only).

Env:
  RATE_LIMIT_PER_MIN — max requests per client IP per 60s window on guarded
    routes (default 300; ``0`` disables). Generous on purpose: it stops
    accidental DoS/abuse of /quote, /bars, /ai/* without breaking tests or
    normal UI polling.
  RATE_LIMIT_EXEMPT_PATHS — comma list of extra exempt prefixes (default: none).

Exempt by default: /health, /, /docs, /redoc, /openapi.json.
Response: 429 + ``Retry-After`` header. ``X-RateLimit-Limit/Remaining`` are
set on guarded responses for observability.
"""

from __future__ import annotations

import os
import time
from collections import deque

from fastapi import HTTPException, Request

_EXEMPT_PREFIXES = ("/health", "/docs", "/redoc", "/openapi.json", "/")

_buckets: dict[str, deque] = {}


def _per_min() -> int:
    try:
        value = int((os.getenv("RATE_LIMIT_PER_MIN", "300") or "300").strip())
    except (TypeError, ValueError):
        return 300
    if value <= 0:
        return 0
    return min(value, 100_000)


def _exempt_extra() -> tuple[str, ...]:
    raw = os.getenv("RATE_LIMIT_EXEMPT_PATHS", "") or ""
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def reset_rate_limiter() -> None:  # test hook
    _buckets.clear()


def _is_exempt(path: str) -> bool:
    if path in ("/health", "/", "/openapi.json") or path.startswith("/docs") or path.startswith("/redoc"):
        return True
    for prefix in _exempt_extra():
        if path == prefix or path.startswith(prefix):
            return True
    return False


def _client_ip(request: Request) -> str:
    try:
        forwarded = request.headers.get("x-forwarded-for", "") or ""
        if forwarded.strip():
            return forwarded.split(",")[0].strip() or "unknown"
    except Exception:
        pass
    try:
        if request.client is not None and request.client.host:
            return str(request.client.host)
    except Exception:
        pass
    return "unknown"


def check_rate_limit(request: Request) -> tuple[int, int] | None:
    """Return (limit, remaining) or None when exempt/disabled. Raises 429."""
    limit = _per_min()
    if limit <= 0:
        return None
    try:
        path = request.url.path if request.url is not None else "/"
    except Exception:
        path = "/"
    if _is_exempt(path):
        return None
    if not path.startswith("/api"):
        return None
    now = time.monotonic()
    window = 60.0
    key = f"{_client_ip(request)}"
    bucket = _buckets.get(key)
    if bucket is None:
        bucket = deque()
        _buckets[key] = bucket
    while bucket and (now - bucket[0]) >= window:
        bucket.popleft()
    if len(bucket) >= limit:
        retry_after = max(1, int(window - (now - bucket[0])))
        raise HTTPException(
            status_code=429,
            detail="rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )
    bucket.append(now)
    # Bound memory: drop idle clients beyond 4096 keys (oldest first).
    if len(_buckets) > 4096:
        try:
            _buckets.pop(next(iter(_buckets)), None)
        except Exception:
            pass
    return limit, max(0, limit - len(bucket))
