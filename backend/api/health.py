"""GET /health — liveness + dependency + provider latency/error summary."""

from __future__ import annotations

import os

from fastapi import APIRouter

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
def health() -> dict:
    """Contract shape: {status, postgres, redis, version} + providers summary."""
    tracker = get_health_tracker()
    providers = tracker.all_stats()
    degraded = any(p.get("circuit") == "open" for p in providers)
    return {
        "status": "degraded" if degraded else "ok",
        "postgres": _check_tcp(os.getenv("DATABASE_URL", ""), "unknown"),
        "redis": "up" if os.getenv("REDIS_URL") else "not-configured",
        "version": backend_version,
        "providers": providers,
    }
