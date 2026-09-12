"""Provider routers: dashboard health per docs/DATA_QUALITY.md + README."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..market_data.health import ProviderHealthTracker
from .deps import get_health_tracker, get_market_service

router = APIRouter(prefix="/api/providers", tags=["providers"])


@router.get("/health")
def providers_health(tracker: ProviderHealthTracker = Depends(get_health_tracker)):
    """GET /api/providers/health -> per-provider latency/error/circuit state."""
    stats = tracker.all_stats()
    if not stats:
        stats = [{
            "provider": "yfinance", "latency_p50_ms": 0.0, "latency_p95_ms": 0.0,
            "error_rate_1h": 0.0, "calls_1h": 0, "total_calls": 0,
            "circuit": "closed", "last_check": None,
        }]
    return {"providers": stats}


@router.post("/health/test")
def test_provider(provider: str = "yfinance", svc=Depends(get_market_service)):
    """Health probe: fetch a reference quote and return the fresh stats."""
    svc.get_quote("AAPL")
    tracker = get_health_tracker()
    stats = tracker.stats(provider)
    if stats["total_calls"] == 0:
        stats = {**stats, "provider": provider}
    return stats
