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


# -- Phase 1b calendar-aware health (library-backed, deterministic) -----------

def test_phase1b_health_us_christmas_closed():
    """2025-12-25 Christmas: health market_state closed for XNYS/XNAS."""
    from zoneinfo import ZoneInfo

    NY = ZoneInfo("America/New_York")
    xmas = datetime(2025, 12, 25, 10, 0, tzinfo=NY)
    assert market_state(xmas, delay_minutes=15, now=xmas, mic="XNYS", at=xmas) == "closed"
    assert market_state(xmas, delay_minutes=15, now=xmas, mic="XNAS", at=xmas) == "closed"
    # Fresh Wednesday session stays open.
    wed = datetime(2025, 9, 3, 10, 0, tzinfo=NY)
    assert market_state(wed, delay_minutes=15, now=wed, mic="XNYS", at=wed) == "open"


def test_phase1b_health_euronext_boxing_day_closed():
    """2025-12-26 Boxing Day: health market_state closed for XPAR."""
    from zoneinfo import ZoneInfo

    PAR = ZoneInfo("Europe/Paris")
    boxing = datetime(2025, 12, 26, 10, 0, tzinfo=PAR)
    assert market_state(boxing, delay_minutes=15, now=boxing, mic="XPAR", at=boxing) == "closed"


# -- Agent 6: enriched health schema + breaker + quota + probing -------------

def test_tracker_stats_enriched_schema_backward_compatible():
    tracker = ProviderHealthTracker()
    tracker.record("yfinance", 120.0, True)
    stats = tracker.stats("yfinance")
    # Legacy keys (frontend HomePage cards + /health contract).
    for key in ("provider", "latency_p50_ms", "latency_p95_ms", "error_rate_1h",
                "calls_1h", "total_calls", "circuit", "last_check"):
        assert key in stats, f"missing legacy key {key!r}"
    # Enriched keys (Agent 6 schema).
    for key in ("kind", "state", "error_rate_5m", "calls_5m", "last_success",
                "consecutive_failures", "quota"):
        assert key in stats, f"missing enriched key {key!r}"
    assert stats["kind"] == "data"
    assert stats["state"] == "up"
    assert stats["quota"]["limited"] is False
    assert stats["consecutive_failures"] == 0
    assert stats["last_success"] is not None


def test_tracker_breaker_opens_after_threshold_and_half_open_recovers():
    tracker = ProviderHealthTracker(failure_threshold=5, open_cooldown_s=60.0)
    for _ in range(5):
        tracker.record("stooq", 50.0, False, error="connection reset")
    assert tracker.get_circuit("stooq") == "open"
    assert tracker.stats("stooq")["state"] == "down"
    assert tracker.stats("stooq")["consecutive_failures"] == 5
    # Cooldown expiry -> half-open probe state (degraded, still servable).
    tracker._opened_at["stooq"] = __import__("time").monotonic() - 61.0
    assert tracker.get_circuit("stooq") == "half-open"
    assert tracker.stats("stooq")["state"] == "degraded"
    # Successful probe closes the breaker.
    tracker.record("stooq", 40.0, True)
    assert tracker.get_circuit("stooq") == "closed"
    assert tracker.stats("stooq")["consecutive_failures"] == 0


def test_tracker_quota_429_degraded_not_down_never_opens():
    tracker = ProviderHealthTracker()
    for _ in range(10):
        tracker.record("yfinance", 30.0, False, status_code=429,
                       error="ProviderError: rate limited (429)")
    stats = tracker.stats("yfinance")
    assert stats["quota"]["limited"] is True
    assert stats["quota"]["reason"] in ("rate_limited", "quota_exceeded")
    assert stats["quota"]["status_code"] == 429
    assert stats["circuit"] == "closed"  # quota never trips the breaker
    assert stats["consecutive_failures"] == 0
    assert stats["state"] == "degraded"  # degraded, NOT down
    # Recovery clears the quota flag.
    tracker.record("yfinance", 25.0, True)
    assert tracker.stats("yfinance")["quota"]["limited"] is False


def test_tracker_ai_unconfigured_state_distinct_from_data():
    tracker = ProviderHealthTracker()
    tracker.record("gemini", 0.0, False, error="unconfigured (no API key)",
                   quota_limited=True)
    tracker.record_quota("gemini", limited=True, reason="unconfigured")
    stats = tracker.stats("gemini")
    assert stats["kind"] == "ai"
    assert stats["state"] == "unconfigured"
    assert stats["circuit"] == "closed"
    # Data providers never report unconfigured.
    tracker2 = ProviderHealthTracker()
    tracker2.record("fx", 0.0, False, error="unconfigured (no API key)",
                    quota_limited=True)
    assert tracker2.stats("fx")["state"] in ("degraded", "unknown", "up")


def test_providers_health_lists_all_known_enriched():
    client = _client()
    resp = client.get("/api/providers/health")
    assert resp.status_code == 200, resp.text
    rows = {p["provider"]: p for p in resp.json()["providers"]}
    for name in ("yfinance", "alpaca", "fx",
                 "gemini", "openai", "anthropic", "xai"):
        assert name in rows, f"missing known provider {name}"
        for key in ("state", "latency_p50_ms", "latency_p95_ms", "error_rate_1h",
                    "error_rate_5m", "last_check", "consecutive_failures", "quota"):
            assert key in rows[name], f"{name} missing {key!r}"


def test_provider_health_test_rejects_unknown_and_pings_ai_unconfigured():
    client = _client()
    bad = client.post("/api/providers/health/test", params={"provider": "bogus"})
    assert bad.status_code == 422, bad.text
    # AI ping without a key short-circuits (no network): unconfigured, 200.
    import os

    for env in ("GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "XAI_API_KEY"):
        os.environ.pop(env, None)
    resp = client.post("/api/providers/health/test", params={"provider": "gemini"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["provider"] == "gemini"
    assert body["quota"]["limited"] is True
    assert body["state"] == "unconfigured"


def test_health_lightweight_and_known_coverage():
    import time

    client = _client()
    started = time.perf_counter()
    resp = client.get("/health")
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    assert resp.status_code == 200, resp.text
    assert elapsed_ms < 1000.0, f"/health took {elapsed_ms:.0f}ms (must stay lightweight)"
    names = {p["provider"] for p in resp.json()["providers"]}
    assert {"yfinance", "fx", "gemini"} <= names

def test_ai_unconfigured_probe_leaves_no_latency_sample(monkeypatch):
    """Unconfigured AI ping marks unconfigured with zero samples (no fake ms)."""
    import os

    from backend.market_data.health import ProviderHealthTracker, probe_ai_provider

    for env in ("GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "XAI_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    tracker = ProviderHealthTracker()
    out = probe_ai_provider("gemini", tracker)
    assert out["state"] == "unconfigured"
    stats = tracker.stats("gemini")
    assert stats["state"] == "unconfigured"
    assert stats["total_calls"] == 0
    assert stats["latency_p50_ms"] is None
    assert stats["latency_p95_ms"] is None


def test_providers_health_probes_zero_sample_providers(monkeypatch):
    """Probe-on-empty pings only sample-less providers (measured, not unknown)."""
    import backend.api.providers as providers_module
    from backend.market_data.health import ProviderHealthTracker

    tracker = ProviderHealthTracker()
    tracker.record("yfinance", 120.0, True)  # sampled -> must NOT be probed
    calls: list[str] = []

    def _fake_probe(name, trk=None, **kw):
        calls.append(name)
        assert trk is tracker
        trk.record(name, 42.0, True)
        return trk.stats(name)

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)  # enable probing
    monkeypatch.setattr(
        "backend.market_data.health.probe_provider", _fake_probe)
    out = providers_module.providers_health(tracker)
    rows = {p["provider"]: p for p in out["providers"]}
    assert "yfinance" not in calls
    assert {"alpaca", "fx", "gemini"} <= set(calls)
    assert rows["alpaca"]["total_calls"] == 1
    assert rows["alpaca"]["latency_p50_ms"] == 42.0
    assert rows["yfinance"]["latency_p50_ms"] == 120.0
