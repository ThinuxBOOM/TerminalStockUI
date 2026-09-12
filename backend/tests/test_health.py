"""Health tests: /health shape, tracker stats, circuit breaker, freshness."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from backend.api.deps import get_health_tracker, reset_deps
from backend.api.main import create_app
from backend.market_data.health import ProviderHealthTracker, freshness_ok, market_state, reconcile_quotes
from backend.market_data.providers.base import CircuitBreaker


def _client() -> TestClient:
    reset_deps()
    return TestClient(create_app())


def test_health_endpoint_shape():
    resp = _client().get("/health")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] in ("ok", "degraded")
    for key in ("postgres", "redis", "version"):
        assert key in body, f"/health missing {key!r}"
    assert "providers" in body  # latency/error summary per task


def test_providers_health_endpoint():
    client = _client()
    client.get("/api/market_data/quote", params={"symbol": "AAPL"})
    resp = client.get("/api/providers/health")
    assert resp.status_code == 200, resp.text
    assert "providers" in resp.json()


def test_tracker_stats_latency_and_errors():
    tracker = ProviderHealthTracker()
    for i in range(10):
        tracker.record("yfinance", 100 + i * 10, ok=(i < 8))
    stats = tracker.stats("yfinance")
    assert stats["provider"] == "yfinance"
    assert stats["latency_p50_ms"] > 0
    assert stats["latency_p95_ms"] >= stats["latency_p50_ms"]
    assert stats["error_rate_1h"] == 0.2
    assert stats["circuit"] == "closed"


def test_circuit_breaker_opens_and_recovers():
    breaker = CircuitBreaker(failure_threshold=3, reset_timeout_s=0.01)
    assert breaker.allow_request()
    for _ in range(3):
        breaker.record_failure()
    assert breaker.state == "open"
    assert not breaker.allow_request()
    import time

    time.sleep(0.02)
    assert breaker.allow_request()  # half-open probe permitted
    breaker.record_success()
    assert breaker.state == "closed"
    assert breaker.allow_request()


def test_health_degraded_when_circuit_open():
    reset_deps()
    tracker = get_health_tracker()
    tracker.set_circuit("yfinance", "open")
    body = TestClient(create_app()).get("/health").json()
    assert body["status"] == "degraded"
    assert any(p["provider"] == "yfinance" and p["circuit"] == "open" for p in body["providers"])


def test_market_state_freshness():
    now = datetime.now(timezone.utc)
    assert market_state(now, delay_minutes=15, now=now) == "open"
    assert market_state(now - timedelta(minutes=30), delay_minutes=15, now=now) == "delayed"
    assert market_state(now - timedelta(days=3), delay_minutes=15, now=now) == "stale"
    assert freshness_ok(now, delay_minutes=15, now=now)
    assert not freshness_ok(now - timedelta(days=3), delay_minutes=15, now=now)


def test_reconcile_quotes_agree_and_diverge():
    ok = reconcile_quotes({"price": 100.0}, {"price": 100.5})
    assert ok["compared"] and ok["agree"]
    bad = reconcile_quotes({"price": 100.0}, {"price": 110.0})
    assert bad["compared"] and not bad["agree"]
    missing = reconcile_quotes({"price": None}, {"price": 100.0})
    assert not missing["compared"]
