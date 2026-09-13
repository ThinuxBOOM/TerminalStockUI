"""Honesty-audit regression tests: provenance envelope + disclosure coverage.

Every data-bearing response must carry the 6-field provenance envelope
``{source, as_of, delay_minutes, quality_grade, fallback_used,
missing_fields}`` and decision-grade outputs must carry the
"Not investment advice" disclosure. Stub/offline paths must stay flagged
(``fallback_used=True`` + honest grade), never silent.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import deps as deps_module
from backend.api.analytics_api import router as analytics_router
from backend.api.audit import router as audit_router
from backend.api.backtest import reset_backtest_history
from backend.api.backtest import router as backtest_router
from backend.api.deps import get_market_service, get_registry, reset_deps
from backend.api.screener import router as screener_router
from backend.cache import InMemoryCache
from backend.db.session import get_db
from backend.forecasting.service import reset_forecast_service
from backend.instruments.registry import InstrumentRegistry
from backend.market_data.fx.convert import compare_cross_market, rank_cross_market
from backend.market_data.health import ProviderHealthTracker
from backend.market_data.providers.yfinance import YFinanceProvider
from backend.market_data.service import MarketDataService

PROVENANCE_KEYS = {
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


def _teardown() -> None:
    reset_deps()
    reset_forecast_service()


# --- analytics ---------------------------------------------------------------

def test_analytics_has_disclosure_and_provenance():
    app = FastAPI()
    app.include_router(analytics_router)
    client = TestClient(app)
    try:
        body = client.get("/api/analytics/AAPL").json()
        assert PROVENANCE_KEYS <= set(body["provenance"])
        assert str(body["disclosure"]).startswith("Not investment advice")
    finally:
        _teardown()


# --- backtest ----------------------------------------------------------------

def test_backtest_run_and_history_have_disclosure():
    reset_backtest_history()
    app = FastAPI()
    app.include_router(backtest_router)
    client = TestClient(app)
    try:
        run = client.post(
            "/api/backtest/run",
            json={"symbol": "AAPL", "horizons": [5],
                  "train_size": 100, "test_size": 21, "gap": 5},
        )
        assert run.status_code == 200, run.text
        assert PROVENANCE_KEYS <= set(run.json()["provenance"])
        assert str(run.json()["disclosure"]).startswith("Not investment advice")
        hist = client.get("/api/backtest/AAPL")
        assert hist.status_code == 200, hist.text
        assert PROVENANCE_KEYS <= set(hist.json()["provenance"])
        assert str(hist.json()["disclosure"]).startswith("Not investment advice")
    finally:
        reset_backtest_history()
        _teardown()


# --- audit -------------------------------------------------------------------

def test_audit_ai_decisions_has_disclosure(tmp_path):
    from backend.api.audit import append_audit_log
    from backend.api.main import create_app
    from backend.db.session import init_db

    url = f"sqlite:///{tmp_path}/honesty_audit.db"
    init_db(url)
    from backend.db.session import get_engine
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=get_engine(url), autoflush=False, expire_on_commit=False)
    with Session() as db:
        append_audit_log(db, actor="system", action="ai.opinion.requested",
                         entity_type="ai_opinion", entity_id="o1",
                         payload={"provider": "gemini"})
    app = create_app()

    def _override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    client = TestClient(app)
    try:
        resp = client.get("/api/audit/ai_decisions")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["count"] >= 1
        assert str(body["disclosure"]).startswith("Not investment advice")
        forecasts = client.get("/api/audit/forecasts").json()
        assert str(forecasts["disclosure"]).startswith("Not investment advice")
    finally:
        app.dependency_overrides.pop(get_db, None)


# --- screener ----------------------------------------------------------------

def _screener_client(svc=None, registry=None) -> TestClient:
    reset_deps()
    reset_forecast_service()
    stub = svc or _stub_service()
    reg = registry or stub.registry
    deps_module._service = stub
    deps_module._registry = reg
    deps_module._health = stub.health
    app = FastAPI()
    app.include_router(screener_router)
    app.dependency_overrides[get_market_service] = lambda: stub
    app.dependency_overrides[get_registry] = lambda: reg
    return TestClient(app)


def test_screener_top_level_provenance_envelope_flagged_on_stub():
    client = _screener_client()
    try:
        resp = client.get("/api/screener", params={"min_direction": 0.0, "limit": 5})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert PROVENANCE_KEYS <= set(body["provenance"]), body["provenance"]
        # Stub market data: the scan-level envelope must stay flagged.
        assert body["provenance"]["fallback_used"] is True
        assert body["provenance"]["quality_grade"] == "C"
        assert str(body["disclosure"]).startswith("Not investment advice")
        for row in body["results"]:
            assert PROVENANCE_KEYS <= set(row["provenance"])
    finally:
        _teardown()


def test_screener_empty_universe_provenance_not_fallback():
    empty = InstrumentRegistry()
    empty._items = []
    client = _screener_client(registry=empty)
    try:
        body = client.get("/api/screener").json()
        assert body["results"] == []
        assert PROVENANCE_KEYS <= set(body["provenance"])
        # Nothing served: honest non-fallback envelope, never stub-flagged.
        assert body["provenance"]["fallback_used"] is False
    finally:
        _teardown()


# --- fx rank/compare ----------------------------------------------------------

def _live_prov():
    from datetime import datetime, timezone

    return {
        "source": "frankfurter", "as_of": datetime.now(timezone.utc).isoformat(),
        "delay_minutes": 15, "quality_grade": "B",
        "fallback_used": False, "missing_fields": [],
    }


def test_fx_rank_and_compare_carry_disclosure_and_provenance():
    items = [
        {"symbol": "AAPL", "price": 100.0, "currency": "USD"},
        {"symbol": "MC.PA", "price": 100.0, "currency": "EUR"},
    ]
    rates = {"EUR/USD": 1.08}
    ranked = rank_cross_market(items, "USD", rates, _live_prov())
    assert PROVENANCE_KEYS <= set(ranked["provenance"])
    assert str(ranked["disclosure"]).startswith("Not investment advice")
    compared = compare_cross_market(items[0], items[1], "USD", rates, _live_prov())
    assert PROVENANCE_KEYS <= set(compared["provenance"])
    assert str(compared["disclosure"]).startswith("Not investment advice")


def test_fx_rank_http_stub_stays_flagged_with_disclosure():
    from fastapi import FastAPI as _FastAPI

    from backend.api import fx as fxapi
    from backend.market_data.fx.provider import FXProvider

    reset_deps()
    fxapi.reset_fx_provider()
    stub_fx = FXProvider(stub_mode=True)
    app = _FastAPI()
    app.include_router(fxapi.router)
    app.dependency_overrides[fxapi.get_fx_provider] = lambda: stub_fx
    app.dependency_overrides[get_market_service] = _stub_service
    client = TestClient(app)
    try:
        # Cross-currency pair forces a (stub) EUR/USD rate into the mix,
        # so the combined envelope must stay flagged even with opt-in.
        resp = client.post("/api/fx/rank", json={
            "symbols": ["AAPL", "MC.PA"], "target_ccy": "USD",
            "allow_fallback": True,
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["provenance"]["fallback_used"] is True
        assert str(body["disclosure"]).startswith("Not investment advice")
    finally:
        fxapi.reset_fx_provider()
        _teardown()


# --- stub-flag assertions ------------------------------------------------------

def test_stub_quote_and_bars_stay_flagged():
    svc = _stub_service()
    try:
        quote = svc.get_quote("ZZZ_UNKNOWN_123")
        assert quote["provenance"]["fallback_used"] is True
        assert quote["provenance"]["quality_grade"] == "C"
        bars = svc.get_bars("ZZZ_UNKNOWN_123", timeframe="1d", limit=10)
        assert bars["provenance"]["fallback_used"] is True
        assert bars["provenance"]["quality_grade"] == "C"
        assert len(bars["bars"]) == 10
    finally:
        _teardown()
