"""GET /api/markets/{mic}/index tests: TestClient, no network, deterministic."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import deps as deps_module
from backend.api.deps import get_market_service, get_registry, reset_deps
from backend.api.market_index import BENCHMARKS, router
from backend.cache import InMemoryCache
from backend.instruments.registry import InstrumentRegistry
from backend.market_data.health import ProviderHealthTracker
from backend.market_data.providers.yfinance import YFinanceProvider
from backend.market_data.service import MarketDataService

REQUIRED_PROVENANCE = {
    "source", "as_of", "delay_minutes",
    "quality_grade", "fallback_used", "missing_fields",
}


def _stub_service() -> MarketDataService:
    tracker = ProviderHealthTracker()
    provider = YFinanceProvider(
        stub_mode=True, on_call=lambda p, ms, ok: tracker.record(p, ms, ok)
    )
    return MarketDataService(
        registry=InstrumentRegistry(), provider=provider,
        health=tracker, cache=InMemoryCache(),
    )


def _client() -> TestClient:
    reset_deps()
    app = FastAPI()
    app.include_router(router)
    svc = _stub_service()
    reg = InstrumentRegistry()
    app.dependency_overrides[get_market_service] = lambda: svc
    app.dependency_overrides[get_registry] = lambda: reg
    deps_module._MARKET_SERVICE = svc  # type: ignore[attr-defined]
    deps_module._REGISTRY = reg  # type: ignore[attr-defined]
    return TestClient(app)


def test_index_registry_covers_six_live_plus_disabled_xcol():
    live = sorted(m for m, c in BENCHMARKS.items() if c.get("enabled") is True)
    assert live == ["XAMS", "XBRU", "XNAS", "XNYS", "XPAR", "XSHG"]
    assert BENCHMARKS["XCOL"]["enabled"] is False
    assert BENCHMARKS["XCOL"]["currency"] == "LKR"


def test_index_xshg_serves_points_or_honest_502():
    c = _client()
    r = c.get("/api/markets/XSHG/index", params={"timeframe": "1d"})
    assert r.status_code in (200, 502)
    if r.status_code == 200:
        body = r.json()
        assert body["mic"] == "XSHG"
        assert body["used_symbol"] == "000001.SS"
        assert body["is_proxy"] is False
        assert body["count"] >= 1
        assert set(body["provenance"]) >= REQUIRED_PROVENANCE
        assert "Not investment advice" in body["disclosure"]


def test_index_xcol_disabled_422_never_fakes():
    c = _client()
    r = c.get("/api/markets/XCOL/index", params={"timeframe": "1d"})
    assert r.status_code == 422
    assert "disabled" in r.json()["detail"].lower()


def test_index_unknown_mic_422_and_bad_timeframe_422():
    c = _client()
    assert c.get("/api/markets/XXXX/index").status_code == 422
    assert c.get("/api/markets/XNYS/index", params={"timeframe": "bogus"}).status_code == 422
