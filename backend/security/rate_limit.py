"""In-memory sliding-window rate limiter (single-process deployment).

Env:
  RATE_LIMIT_PER_MIN       general /api/* budget per client per 60s (default 300; 0 disables)
  AUTH_RATE_LIMIT_PER_MIN  budget for login/register/refresh per client (default 10)

The client is ``request.client.host``. Behind the Caddy reverse proxy that is
the real visitor address because uvicorn runs with ``--proxy-headers
--forwarded-allow-ips`` limited to the proxy; a raw ``X-Forwarded-For`` from
the internet is never trusted here.
"""

from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict, deque

from fastapi import HTTPException, Request

_WINDOW_S = 60.0
_MAX_CLIENTS = 10_000
_AUTH_PATHS = ("/api/auth/login", "/api/auth/register", "/api/auth/refresh")

_buckets: "OrderedDict[str, deque]" = OrderedDict()
_lock = threading.Lock()


def _env_int(name: str, default: int) -> int:
    try:
        value = int((os.getenv(name, str(default)) or str(default)).strip())
    except ValueError:
        return default
    return max(0, min(value, 100_000))


def reset_rate_limiter() -> None:  # test hook
    with _lock:
        _buckets.clear()


def _client_ip(request: Request) -> str:
    client = request.client
    return str(client.host) if client is not None and client.host else "unknown"


def check_rate_limit(request: Request) -> tuple[int, int] | None:
    """Return (limit, remaining) for guarded paths, None when exempt. Raises 429."""
    path = request.url.path
    if not path.startswith("/api"):
        return None
    if path in _AUTH_PATHS:
        scope, limit = "auth", _env_int("AUTH_RATE_LIMIT_PER_MIN", 10)
    else:
        scope, limit = "api", _env_int("RATE_LIMIT_PER_MIN", 300)
    if limit <= 0:
        return None
    key = f"{scope}:{_client_ip(request)}"
    now = time.monotonic()
    with _lock:
        bucket = _buckets.get(key)
        if bucket is None:
            bucket = deque()
            _buckets[key] = bucket
        _buckets.move_to_end(key)
        while bucket and now - bucket[0] >= _WINDOW_S:
            bucket.popleft()
        if len(bucket) >= limit:
            retry_after = max(1, int(_WINDOW_S - (now - bucket[0])))
            raise HTTPException(
                status_code=429, detail="rate limit exceeded", headers={"Retry-After": str(retry_after)},
            )
        bucket.append(now)
        while len(_buckets) > _MAX_CLIENTS:
            _buckets.popitem(last=False)  # least recently seen client
        return limit, max(0, limit - len(bucket))
