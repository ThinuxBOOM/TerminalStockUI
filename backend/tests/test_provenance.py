"""Provenance tests: every data response carries the full envelope (spec section 4).

Fail-closed contract: MarketDataService serves ONLY live data
(fallback_used falsy, price present) else raises ProviderError. Live
doubles are built by taking the deterministic stub quotes, setting
fallback_used=False, popping the fallback marker and ensuring a price.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_market_service, reset_deps
from backend.api.main import create_app
from backend.cache import InMemoryCache
from backend.instruments.registry import InstrumentRegistry
from backend.market_data.health import ProviderHealthTracker
from backend.market_data.providers.base import ProviderError
from backend.market_data.providers.yfinance import YFinanceProvider
from backend.market_data.service import MarketDataService

REQUIRED_KEYS = {"source", "as_of", "delay_minutes", "quality_grade", "fallback_used", "missing_fields"}


def _live_service() -> MarketDataService:
    """Live double: deterministic stub quotes marked live (no network)."""
    tracker = ProviderHealthTracker()
    provider = YFinanceProvider(
        stub_mode=True, on_call=lambda p, ms, ok: tracker.record(p, ms, ok)
    )
    _orig = provider.get_quote

    def _live_get_quote(symbol: str, *args, **kwargs) -> dict:  # type: ignore[no-untyped-def]
        upper = (symbol or "").strip().upper() if isinstance(symbol, str) else ""
        if not upper:
            return _orig(symbol, *args, **kwargs)
        q = dict(_orig(symbol, *args, **kwargs))
        q["fallback_used"] = False
        q.pop("fallback", None)
        q.pop("circuit_open", None)
        if q.get("price") is None:
            q["price"] = 150.0
        return q

    provider.get_quote = _live_get_quote  # type: ignore[method-assign]
    return MarketDataService(registry=InstrumentRegistry(), provider=provider,
                             health=tracker, cache=InMemoryCache())


def _outage_service() -> MarketDataService:
    """Pure outage double: stub fallback without live conversion (fail-closed)."""
    tracker = ProviderHealthTracker()
    provider = YFinanceProvider(
        stub_mode=True, on_call=lambda p, ms, ok: tracker.record(p, ms, ok)
    )
    return MarketDataService(registry=InstrumentRegistry(), provider=provider,
                             health=tracker, cache=InMemoryCache())


def _stub_service() -> MarketDataService:
    # Compat alias: historic name now returns the live double.
    return _live_service()


def _client() -> TestClient:
    from backend.tests.auth_helpers import inject_admin_auth

    reset_deps()
    app = create_app()
    app.dependency_overrides[get_market_service] = _live_service
    inject_admin_auth(app)
    return TestClient(app)


def test_provenance_envelope_complete_on_quote():
    svc = _live_service()
    out = svc.get_quote("AAPL")
    prov = out["provenance"]
    assert REQUIRED_KEYS <= set(prov), f"missing provenance keys: {REQUIRED_KEYS - set(prov)}"
    assert prov["source"] == "yfinance"
    assert isinstance(prov["delay_minutes"], int)
    assert prov["quality_grade"] in ("A", "B", "C", "D", "F")
    assert isinstance(prov["fallback_used"], bool)
    assert isinstance(prov["missing_fields"], list)
    # Fail-closed: live only.
    assert prov["fallback_used"] is False
    assert out["price"] is not None


def test_provenance_marks_stub_fallback():
    # Provider-level stub still flags fallback; the service is fail-closed
    # and refuses to serve it (raises ProviderError). Live doubles are
    # fallback-free.
    raw_provider = YFinanceProvider(stub_mode=True)
    raw = raw_provider.get_quote("AAPL")
    assert raw["fallback_used"] is True
    svc = _outage_service()  # stub fallback, no live conversion
    with pytest.raises(ProviderError):
        svc.get_quote("AAPL")
    live = _live_service()
    out = live.get_quote("AAPL")
    assert out["provenance"]["fallback_used"] is False
    assert out["provenance"]["quality_grade"] in ("A", "B")


def test_quote_http_carries_provenance():
    client = _client()
    resp = client.get("/api/market_data/quote", params={"symbol": "AAPL"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert REQUIRED_KEYS <= set(body["provenance"])
    assert body["provenance"]["fallback_used"] is False
    assert body["price"] is not None


def test_quote_outage_maps_to_502():
    """Routers map ProviderError -> 502 (fail-closed, never stale)."""
    reset_deps()
    app = create_app()
    app.dependency_overrides[get_market_service] = _outage_service
    client = TestClient(app)
    try:
        resp = client.get("/api/market_data/quote", params={"symbol": "AAPL"})
        assert resp.status_code == 502, resp.text
    finally:
        reset_deps()


def test_search_http_carries_provenance():
    client = _client()
    resp = client.get("/api/instruments/search", params={"q": "AAPL"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["results"], "expected at least one result for AAPL"
    # Search is registry-local: source + zero delay + grade A.
    assert body["results"][0]["exchange_symbol"] == "AAPL"
    assert body.get("provenance", {}).get("source") == "instrument-registry"


def test_bars_http_carries_provenance():
    client = _client()
    resp = client.get("/api/market_data/bars", params={"symbol": "AAPL", "limit": 5})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["bars"]) == 5
    assert REQUIRED_KEYS <= set(body["provenance"])
    # DB bars are live (fail-closed): never fallback.
    assert body["provenance"]["fallback_used"] is False
    for row in body["bars"]:
        assert row["close"] is not None


# --- Agent 6: health enrichment leaves the provenance envelope untouched -----

def test_passive_health_records_do_not_alter_provenance():
    svc = _live_service()
    out = svc.get_quote("AAPL")
    assert REQUIRED_KEYS <= set(out["provenance"])
    assert out["provenance"]["fallback_used"] is False
    stats = svc.health.stats("yfinance")
    assert stats["total_calls"] >= 1
    # Enriched keys exist alongside the legacy shape.
    for key in ("state", "error_rate_5m", "calls_5m", "last_success",
                "consecutive_failures", "quota", "kind"):
        assert key in stats
    # A quota-limited mark degrades health but never rewrites provenance.
    svc.health.record("yfinance", 5.0, False, status_code=429,
                      error="rate limited (429)")
    assert svc.health.stats("yfinance")["quota"]["limited"] is True
    assert svc.health.stats("yfinance")["state"] == "degraded"
    out2 = svc.get_quote("AAPL")
    assert REQUIRED_KEYS <= set(out2["provenance"])
    assert out2["provenance"]["fallback_used"] is False


def test_providers_health_rows_carry_enriched_schema():
    from backend.tests.auth_helpers import inject_admin_auth

    reset_deps()
    app = create_app()
    app.dependency_overrides[get_market_service] = _live_service
    from fastapi.testclient import TestClient

    inject_admin_auth(app)
    client = TestClient(app)
    client.get("/api/market_data/quote", params={"symbol": "AAPL"})
    resp = client.get("/api/providers/health")
    assert resp.status_code == 200, resp.text
    rows = {p["provider"]: p for p in resp.json()["providers"]}
    assert "yfinance" in rows
    for key in ("state", "error_rate_5m", "calls_5m", "consecutive_failures", "quota"):
        assert key in rows["yfinance"], f"yfinance row missing {key!r}"
