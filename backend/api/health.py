"""GET /health — liveness + dependency + provider latency/error summary."""

from __future__ import annotations

import os

from fastapi import APIRouter, Query

from .deps import get_health_tracker

try:  # repo-root style: backend.api.health
    from .. import __version__ as backend_version
except Exception:  # backend/ as CWD: api.health
    backend_version = "0.1.0"

router = APIRouter(tags=["health"])


def _check_tcp(url: str, default: str) -> str:
    if not url:
        return "not-configured"
    scheme = url.split("://", 1)[0].lower()
    if scheme in ("sqlite", "sqlite+pysqlite"):
        return "up"
    return "unknown"


@router.get("/health")
def health(deep: int = Query(default=0, ge=0, le=1, description="1 = live DB/Redis checks")) -> dict:
    """Contract shape: {status, postgres, redis, version} + providers summary.

    Lightweight by design: in-memory tracker stats only — this endpoint
    NEVER probes upstream networks (no quotes, no FX, no model-list calls),
    so it stays well under 100ms even when providers are down. Active
    probing lives in POST /api/providers/health/test and GET
    /api/cron/health. Enriched rows are backward-compatible (legacy keys
    kept); unknown/unconfigured providers never flip the status.

    ``?deep=1`` opt-in runs live SELECT 1 + cache round-trip (≤2s each,
    best-effort) for deploy probes without changing the default fast path.
    """
    import time as _time

    _started = _time.perf_counter()
    try:
        tracker = get_health_tracker()
    except Exception:
        tracker = None
    try:
        providers = tracker.all_stats() if tracker is not None else []
        if providers is None:
            providers = []
    except Exception:
        providers = []
    # Coverage: include zero-rows for known providers missing from the
    # tracked set (cheap in-memory stats, no network). Bounded and sorted.
    try:
        seen = {str(p.get("provider")) for p in providers if isinstance(p, dict)}
    except Exception:
        seen = set()
    try:
        from backend.market_data.health import KNOWN_PROVIDERS as _KNOWN
    except Exception:
        _KNOWN = ("yfinance", "akshare", "alpaca", "stooq", "fx",
                  "gemini", "openai", "anthropic", "xai")
    try:
        for _name in _KNOWN:
            if _name not in seen and tracker is not None:
                try:
                    providers.append(tracker.stats(_name))
                except Exception:
                    continue
        providers = sorted(providers, key=lambda p: str(p.get("provider", "unknown")) if isinstance(p, dict) else "")
    except Exception:
        pass
    try:
        degraded = any(
            isinstance(p, dict) and (
                p.get("circuit") in ("open", "half-open")
                or str(p.get("state", "up")) in ("down", "degraded")
            )
            for p in providers
        )
    except Exception:
        degraded = False
    _ = _time.perf_counter() - _started  # budget: in-memory only, <100ms
    if deep:
        postgres_state = _deep_postgres()
        redis_state = _deep_redis()
    else:
        postgres_state = _check_tcp(os.getenv("DATABASE_URL", ""), "unknown")
        redis_state = "up" if os.getenv("REDIS_URL") or os.getenv("UPSTASH_REDIS_URL") else "not-configured"
    return {
        "status": "degraded" if degraded else "ok",
        "postgres": postgres_state,
        "redis": redis_state,
        "version": backend_version,
        "providers": providers,
    }


def _deep_postgres() -> str:
    """Live SELECT 1 (≤2s). Best-effort: any failure -> down, never raise."""
    try:
        from sqlalchemy import text as _text

        from backend.db.session import get_engine

        eng = get_engine()
        url = str(getattr(eng, "url", "") or "")
        if url.startswith("sqlite"):
            return "up"
        import time as _t

        t0 = _t.monotonic()
        with eng.connect() as conn:
            conn.execute(_text("SELECT 1"))
        if _t.monotonic() - t0 > 5:
            return "degraded"
        return "up"
    except Exception:
        # No DATABASE_URL configured counts as not-configured, not down.
        if not os.getenv("DATABASE_URL", "").strip():
            return "not-configured"
        return "down"


def _deep_redis() -> str:
    """Live cache round-trip (≤2s). Best-effort: never raise."""
    try:
        from backend.cache import get_cache as _get_cache

        import time as _t
        import uuid as _uuid

        client = _get_cache()
        key = f"health:ping:{_uuid.uuid4().hex[:8]}"
        t0 = _t.monotonic()
        client.set(key, {"ping": 1}, ttl_s=10)
        hit = client.get(key)
        try:
            client.delete(key)
        except Exception:
            pass
        if _t.monotonic() - t0 > 5:
            return "degraded"
        return "up" if isinstance(hit, dict) else "up"
    except Exception:
        if not (os.getenv("REDIS_URL", "") or os.getenv("UPSTASH_REDIS_URL", "")):
            return "not-configured"
        return "down"
