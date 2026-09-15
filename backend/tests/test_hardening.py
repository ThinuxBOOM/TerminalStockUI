"""Hardening regression tests (audit remediation).

Covers, offline only:
- opt-in API-key auth (open by default, 401 without key when set)
- sliding-window rate limiting (429 + Retry-After)
- security headers + request body cap + CORS allow-list
- symbol / timeframe / instrument_id validation (422, no traversal)
- alerts list pagination (limit/offset/total)
- SECRET_KEY placeholder detection
- deterministic stub-bar helpers removed (fail-closed, no synthesis) + provisional-instrument cache
- AI router cache TTL expiry
"""

from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from backend.ai.router import AIRouter
from backend.api.deps import reset_deps
from backend.api.main import create_app
from backend.db.session import init_db, reset_engine
from backend.market_data.service import MarketDataService
from backend.security import auth as auth_module
from backend.security import rate_limit as rate_limit_module
from backend.security.secrets import is_default_secret_key, reset_fernet


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("API_KEY", "RATE_LIMIT_PER_MIN", "MAX_REQUEST_BYTES", "CORS_ORIGINS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("SECRET_KEY", "test-only-secret-key-for-unit-tests-123")
    reset_fernet()
    rate_limit_module.reset_rate_limiter()
    reset_deps()
    yield
    rate_limit_module.reset_rate_limiter()
    reset_deps()
    reset_fernet()


def _client() -> TestClient:
    reset_deps()
    return TestClient(create_app())


# --- auth -------------------------------------------------------------------


def test_auth_open_by_default():
    assert auth_module.auth_enabled() is False
    resp = _client().get("/api/providers/health")
    assert resp.status_code == 200, resp.text


def test_auth_enforced_when_key_set(monkeypatch):
    monkeypatch.setenv("API_KEY", "prod-key-abc")
    assert auth_module.auth_enabled() is True
    client = _client()
    assert client.get("/api/providers/health").status_code == 401
    assert client.get("/health").status_code == 200  # liveness stays open
    ok = client.get("/api/providers/health", headers={"X-API-Key": "prod-key-abc"})
    assert ok.status_code == 200, ok.text
    bearer = client.get(
        "/api/providers/health", headers={"Authorization": "Bearer prod-key-abc"}
    )
    assert bearer.status_code == 200, bearer.text


def test_auth_key_rotation_list(monkeypatch):
    monkeypatch.setenv("API_KEY", "old-key,new-key")
    client = _client()
    assert client.get("/api/providers/health", headers={"X-API-Key": "old-key"}).status_code == 200
    assert client.get("/api/providers/health", headers={"X-API-Key": "new-key"}).status_code == 200
    assert client.get("/api/providers/health", headers={"X-API-Key": "nope"}).status_code == 401


# --- rate limiting ------------------------------------------------------------


def test_rate_limit_429_with_retry_after(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MIN", "2")
    rate_limit_module.reset_rate_limiter()
    client = _client()
    assert client.get("/api/providers/health").status_code == 200
    assert client.get("/api/providers/health").status_code == 200
    limited = client.get("/api/providers/health")
    assert limited.status_code == 429, limited.text
    assert "Retry-After" in limited.headers


def test_rate_limit_exempts_health(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MIN", "1")
    rate_limit_module.reset_rate_limiter()
    client = _client()
    for _ in range(5):
        assert client.get("/health").status_code == 200


def test_rate_limit_disabled_at_zero(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MIN", "0")
    rate_limit_module.reset_rate_limiter()
    client = _client()
    for _ in range(5):
        assert client.get("/api/providers/health").status_code == 200


# --- headers / body cap / CORS -------------------------------------------------


def test_security_headers_present():
    resp = _client().get("/health")
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert resp.headers.get("X-Frame-Options") == "DENY"
    assert "frame-ancestors" in resp.headers.get("Content-Security-Policy", "")


def test_body_too_large_is_413(monkeypatch):
    monkeypatch.setenv("MAX_REQUEST_BYTES", "100")
    client = _client()
    big = {"symbol": "AAPL", "condition": "price_above", "threshold": 1.0,
           "pad": "x" * 5000}
    resp = client.post("/api/alerts/", json=big)
    assert resp.status_code == 413, resp.text


def test_cors_allowlists_local_dev_by_default():
    resp = _client().get("/health", headers={"Origin": "http://localhost:5173"})
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_cors_rejects_unknown_origin():
    resp = _client().get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in resp.headers


# --- input validation -----------------------------------------------------------


def test_quote_rejects_markup_symbol():
    resp = _client().get("/api/market_data/quote", params={"symbol": "<img src=x>"})
    assert resp.status_code == 422, resp.text


def test_quote_rejects_traversal_symbol():
    resp = _client().get("/api/market_data/quote", params={"symbol": "../../etc"})
    assert resp.status_code == 422, resp.text


def test_bars_rejects_bad_timeframe():
    resp = _client().get(
        "/api/market_data/bars", params={"symbol": "AAPL", "timeframe": "../../x"}
    )
    assert resp.status_code == 422, resp.text


def test_securities_rejects_bad_instrument_id():
    resp = _client().get("/api/securities/../../quote")
    assert resp.status_code in (404, 422)


def test_ai_rejects_bad_symbol():
    resp = _client().post(
        "/api/ai/insight", json={"symbol": "AAPL; DROP TABLE", "profile": "quick_insight"}
    )
    assert resp.status_code == 422, resp.text


def test_alerts_reject_bad_symbol():
    client = _client()
    resp = client.post(
        "/api/alerts/",
        json={"symbol": "AAPL|rm -rf", "condition": "price_above", "threshold": 1.0},
    )
    assert resp.status_code == 422, resp.text


# --- alerts pagination ------------------------------------------------------------


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path}/hardening.db"
    monkeypatch.setenv("DATABASE_URL", url)
    reset_engine()
    init_db(url)
    try:
        yield url
    finally:
        reset_engine()


def test_alerts_list_pagination(isolated_db):
    client = _client()
    for threshold in (10.0, 20.0, 30.0):
        resp = client.post(
            "/api/alerts/",
            json={"symbol": "AAPL", "condition": "price_above", "threshold": threshold},
        )
        assert resp.status_code == 200, resp.text
    first = client.get("/api/alerts/", params={"limit": 2, "offset": 0})
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["count"] == 2
    assert body["total"] == 3
    second = client.get("/api/alerts/", params={"limit": 2, "offset": 2})
    assert second.json()["count"] == 1
    assert second.json()["total"] == 3


# --- secrets ---------------------------------------------------------------------


def test_default_secret_key_detected(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "change-me-generate-with-openssl-rand-hex-32")
    reset_fernet()
    assert is_default_secret_key() is True
    monkeypatch.setenv("SECRET_KEY", "a" * 64)
    reset_fernet()
    assert is_default_secret_key() is False


# --- performance caches -------------------------------------------------------------


def test_stub_base_rows_removed_fail_closed():
    """NO FALLBACKS: deterministic stub-bar helpers are removed (no synthesis)."""
    import backend.market_data.service as _svc_mod

    assert not hasattr(_svc_mod, "_stub_base_rows"), "stub bar cache must not exist"
    assert not hasattr(MarketDataService, "_stub_bars"), "stub bars method must not exist"


def test_bars_fail_closed_without_live_data():
    """NO FALLBACKS: get_bars raises when DB thin/empty and live fetch misses."""
    from backend.market_data.providers.base import ProviderError
    import pytest as _pt

    svc = MarketDataService()
    with _pt.raises(ProviderError):
        svc.get_bars("ZZZNOPE123XYZ", "1d", 5)


def test_provisional_instrument_returns_copies():
    from backend.market_data.service import _provisional_instrument

    first = _provisional_instrument("googl")
    second = _provisional_instrument("GOOGL")
    assert first is not None and second is not None
    assert first is not second  # defensive copy, shared cache underneath
    assert first.instrument_id == second.instrument_id == "XNAS-GOOGL"


def test_ai_cache_ttl_expiry():
    from backend.ai.providers.base import BaseProvider
    from backend.ai.schemas import AIOpinion

    calls = {"n": 0}

    class CountingProvider(BaseProvider):
        name = "gemini"
        default_model = "gemini-3.7-flash"

        async def insight(self, packet, *, profile="quick_insight", horizon=None):
            calls["n"] += 1
            return AIOpinion(
                direction="neutral", probability=0.5,
                time_horizon_days=horizon or 21,
                catalysts=["trend"], risks=["valuation"],
                evidence_ids=packet.evidence_ids or ["ev-1"],
                limitations=["Not investment advice."],
                provider=self.name, model=self.model,
            )

    from backend.ai.evidence import build_evidence_packet

    # Isolate from the process-global dist cache (same reason as the
    # empty_store fixture in test_ai_router.py): a stale dist copy from
    # another test would serve the refetch below without a provider call.
    try:
        from backend.cache import get_cache as _get_cache

        _clear = getattr(_get_cache(), "clear", None)
        if callable(_clear):
            _clear()
    except Exception:
        pass

    router = AIRouter(
        providers={"gemini": CountingProvider(secret_store=None)},
        cache_ttl_s=60,
    )
    packet = build_evidence_packet(
        "AAPL", {"quote": {"price": 1.0}}, {"source": "t", "quality_grade": "B"}
    )
    asyncio.run(router.get_insight(packet, profile="quick_insight"))
    asyncio.run(router.get_insight(packet, profile="quick_insight"))
    assert calls["n"] == 1  # TTL hit
    # Force expiry by backdating the entry, then confirm a refetch happens.
    key = next(iter(router._cache))
    _, opinion = router._cache[key]
    router._cache[key] = (time.monotonic() - 1.0, opinion)
    asyncio.run(router.get_insight(packet, profile="quick_insight"))
    assert calls["n"] == 2


def test_single_query_bars_path_serves_db_rows(isolated_db):
    """Seeded DB rows are served via the join path with honest provenance."""
    from datetime import datetime, timedelta, timezone

    from backend.db.models import Instrument as DBInstrument
    from backend.db.models import PriceBar
    from backend.db.session import get_session_factory

    Session = get_session_factory()
    db = Session()
    try:
        inst = DBInstrument(
            exchange_mic="XNAS", exchange_symbol="AAPL", provider_symbol="AAPL",
            company_name="Apple", currency="USD",
        )
        db.add(inst)
        db.flush()
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for day in range(129):
            db.add(PriceBar(
                instrument_id=inst.instrument_id,
                ts=base + timedelta(days=day),
                timeframe="1d",
                open=100.0 + day, high=101.0 + day, low=99.0 + day,
                close=100.5 + day, volume=1000,
                source="yfinance",
                as_of=datetime(2026, 6, 1, tzinfo=timezone.utc),
                quality_grade="B",
            ))
        db.commit()
    finally:
        db.close()

    svc = MarketDataService()
    out = svc._get_bars_from_db("AAPL", "1d", 30)
    assert out is not None
    assert len(out["bars"]) == 30
    assert out["provenance"]["fallback_used"] is False
    closes = [b["close"] for b in out["bars"]]
    assert closes == sorted(closes)  # ascending after DESC+reverse
