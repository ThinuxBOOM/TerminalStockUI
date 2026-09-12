"""Provenance tests: every data response carries the full envelope (spec section 4)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api.deps import get_market_service, reset_deps
from backend.api.main import create_app
from backend.cache import InMemoryCache
from backend.instruments.registry import InstrumentRegistry
from backend.market_data.health import ProviderHealthTracker
from backend.market_data.providers.yfinance import YFinanceProvider
from backend.market_data.service import MarketDataService

REQUIRED_KEYS = {"source", "as_of", "delay_minutes", "quality_grade", "fallback_used", "missing_fields"}


def _stub_service() -> MarketDataService:
    tracker = ProviderHealthTracker()
    provider = YFinanceProvider(
        stub_mode=True, on_call=lambda p, ms, ok: tracker.record(p, ms, ok)
    )
    return MarketDataService(registry=InstrumentRegistry(), provider=provider,
                             health=tracker, cache=InMemoryCache())


def _client() -> TestClient:
    reset_deps()
    app = create_app()
    app.dependency_overrides[get_market_service] = _stub_service
    return TestClient(app)


def test_provenance_envelope_complete_on_quote():
    svc = _stub_service()
    out = svc.get_quote("AAPL")
    prov = out["provenance"]
    assert REQUIRED_KEYS <= set(prov), f"missing provenance keys: {REQUIRED_KEYS - set(prov)}"
    assert prov["source"] == "yfinance"
    assert isinstance(prov["delay_minutes"], int)
    assert prov["quality_grade"] in ("A", "B", "C", "D", "F")
    assert isinstance(prov["fallback_used"], bool)
    assert isinstance(prov["missing_fields"], list)


def test_provenance_marks_stub_fallback():
    svc = _stub_service()  # stub_mode -> outage fallback path
    out = svc.get_quote("AAPL")
    assert out["provenance"]["fallback_used"] is True
    assert out["provenance"]["quality_grade"] == "C"  # fallback/cached per DATA_QUALITY.md


def test_quote_http_carries_provenance():
    client = _client()
    resp = client.get("/api/market_data/quote", params={"symbol": "AAPL"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert REQUIRED_KEYS <= set(body["provenance"])


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
