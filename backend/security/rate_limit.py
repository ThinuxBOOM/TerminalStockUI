"""In-memory per-IP, per-route rate limiting (stdlib only, no slowapi dep).

Token-bucket per (client_ip, route_prefix): expensive deterministic routes
(screener, markets overview, fx rank, cron, backtest run) get tight budgets;
cheap quote/bars routes get generous ones. Exceeded budgets return 429 with
``Retry-After``; counters expire automatically (no Redis needed for v1
single-replica; multi-replica deployments should front with edge limits).

Limits are deliberately conservative to preserve the offline stub path:
tests and local dev never hit them (budgets >> test-suite call counts).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

# route prefix -> (max_requests, window_seconds)
LIMITS: dict[str, tuple[int, int]] = {
    "/api/screener": (30, 60),
    "/api/markets/overview": (30, 60),
    "/api/markets/": (60, 60),
    "/api/fx/rank": (60, 60),
    "/api/backtest/run": (30, 60),
    "/api/forecast": (120, 60),
    "/api/analytics": (120, 60),
    "/api/cron/": (20, 60),
    "/api/providers/keys": (10, 60),
    "/api/providers/budget": (10, 60),
    "default": (300, 60),
}

_buckets: dict[tuple[str, str], list[float]] = {}
_MAX_BUCKETS = 4096


@dataclass
class RateLimitDecision:
    allowed: bool
    retry_after_s: int = 0
    limit: int = 0
    remaining: int = 0


def _match_limit(path: str) -> tuple[int, int]:
    for prefix, (maximum, window) in LIMITS.items():
        if prefix != "default" and path.startswith(prefix):
            return maximum, window
    return LIMITS["default"]


def check_rate_limit(client_ip: str, path: str, *, now: float | None = None) -> RateLimitDecision:
    maximum, window = _match_limit(path or "/")
    route = "default"
    for prefix in LIMITS:
        if prefix != "default" and (path or "/").startswith(prefix):
            route = prefix
            break
    key = (client_ip or "unknown", route)
    ts = now if now is not None else time.monotonic()
    try:
        hits = _buckets.get(key)
        if hits is None:
            hits = []
            if len(_buckets) >= _MAX_BUCKETS:
                oldest = next(iter(_buckets))
                _buckets.pop(oldest, None)
            _buckets[key] = hits
        cutoff = ts - window
        while hits and hits[0] <= cutoff:
            hits.pop(0)
        if len(hits) >= maximum:
            retry_after = int(max(1, hits[0] + window - ts))
            return RateLimitDecision(False, retry_after, maximum, 0)
        hits.append(ts)
        return RateLimitDecision(True, 0, maximum, maximum - len(hits))
    except Exception:
        return RateLimitDecision(True, 0, maximum, maximum)


def reset_rate_limits() -> None:  # test hook
    _buckets.clear()


__all__ = ["LIMITS", "RateLimitDecision", "check_rate_limit", "reset_rate_limits"]
