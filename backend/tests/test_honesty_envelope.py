"""Honesty-audit regression tests: fail-closed provenance envelope.

Every data-bearing live response carries the 6-field provenance envelope
``{source, as_of, delay_minutes, quality_grade, fallback_used,
missing_fields}`` with ``fallback_used=False``, and decision-grade outputs
carry the "Not investment advice" disclosure. Outages never produce
flagged 200s: live data is served or the request raises / maps to
502/503/423 — never a silent stub.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

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
from backend.market_data.providers.base import ProviderError
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


def _live_service() -> MarketDataService:
    """Live double: stub quotes flipped to fallback_used False (price present)."""
    tracker = ProviderHealthTracker()
    base = YFinanceProvider(
        stub_mode=True, on_call=lambda p, ms, ok: tracker.record(p, ms, ok)
    )
    _orig = base.get_quote

    def _live(symbol: str, *args, **kwargs) -> dict:  # noqa: ANN002, ANN003, ANN202
        q = dict(_orig(symbol))
        q["fallback_used"] = False
        q.pop("fallback", None)
        return q

    base.get_quote = _live  # type: ignore[method-assign]
    return MarketDataService(
        registry=InstrumentRegistry(), provider=base,
        health=tracker, cache=InMemoryCache(),
    )


def _live_fx_provider(*, stale_hours: float | None = None):
    from backend.market_data.fx.provider import FXProvider, stub_rate as _stub

    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)

    prov = FXProvider(stub_mode=False)

    def _live(base: str, quote: str) -> dict:
        asof = _utcnow() - timedelta(hours=stale_hours) if stale_hours else _utcnow()
        return {
            "base": base, "quote": quote, "rate": _stub(base, quote),
            "as_of": asof, "source": "frankfurter",
        }

    prov._fetch_raw = _live  # type: ignore[method-assign]
    prov._get_ecb_table = lambda: None  # type: ignore[method-assign]
    return prov


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
    try:
        from backend.cache import get_cache

        get_cache().clear()
    except Exception:
        pass
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


def test_screener_outage_is_honest_empty_not_flagged():
    """Fail-closed: outage yields empty results + honest non-fallback
    envelope + skipped reasons — never a flagged stub 200 with fake rows."""
    client = _screener_client(svc=_stub_service())
    try:
        resp = client.get("/api/screener", params={"min_direction": 0.0, "limit": 5})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert PROVENANCE_KEYS <= set(body["provenance"]), body["provenance"]
        assert body["provenance"]["fallback_used"] is False
        assert body["results"] == []
        assert len(body.get("skipped", [])) > 0
        assert str(body["disclosure"]).startswith("Not investment advice")
    finally:
        _teardown()


def test_screener_live_carries_fallback_false():
    """Live screener rows and the scan envelope all carry fallback False."""
    client = _screener_client(svc=_live_service())
    try:
        resp = client.get("/api/screener", params={"min_direction": 0.0, "limit": 5})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert PROVENANCE_KEYS <= set(body["provenance"])
        assert body["provenance"]["fallback_used"] is False
        assert str(body["disclosure"]).startswith("Not investment advice")
        assert len(body["results"]) > 0
        for row in body["results"]:
            assert PROVENANCE_KEYS <= set(row["provenance"])
            assert row["provenance"]["fallback_used"] is False
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


def test_fx_rank_http_fail_closed_live_200_outage_502_stale_423():
    """Fail-closed wire invariant: live 200 fallback False; stub outage 502;
    stale 423 — never a flagged fallback 200."""
    from fastapi import FastAPI as _FastAPI

    from backend.api import fx as fxapi
    from backend.market_data.fx.provider import FXProvider

    # Live: 200 with fallback False + disclosure.
    reset_deps()
    fxapi.reset_fx_provider()
    live_fx = _live_fx_provider()
    app = _FastAPI()
    app.include_router(fxapi.router)
    app.dependency_overrides[fxapi.get_fx_provider] = lambda: live_fx
    app.dependency_overrides[get_market_service] = _live_service
    client = TestClient(app)
    try:
        resp = client.post("/api/fx/rank", json={
            "symbols": ["AAPL", "MC.PA"], "target_ccy": "USD",
            "allow_fallback": True,
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["provenance"]["fallback_used"] is False
        assert str(body["disclosure"]).startswith("Not investment advice")
    finally:
        fxapi.reset_fx_provider()
        _teardown()

    # Stub outage: cross-currency rank cannot be served -> 502.
    reset_deps()
    fxapi.reset_fx_provider()
    stub_fx = FXProvider(stub_mode=True)
    app2 = _FastAPI()
    app2.include_router(fxapi.router)
    app2.dependency_overrides[fxapi.get_fx_provider] = lambda: stub_fx
    app2.dependency_overrides[get_market_service] = _live_service
    client2 = TestClient(app2)
    try:
        resp2 = client2.post("/api/fx/rank", json={
            "symbols": ["AAPL", "MC.PA"], "target_ccy": "USD",
            "allow_fallback": True,
        })
        assert resp2.status_code == 502, resp2.text
    finally:
        fxapi.reset_fx_provider()
        _teardown()

    # Stale live rates: 423 even with the flag.
    reset_deps()
    fxapi.reset_fx_provider()
    stale_fx = _live_fx_provider(stale_hours=30.0)
    app3 = _FastAPI()
    app3.include_router(fxapi.router)
    app3.dependency_overrides[fxapi.get_fx_provider] = lambda: stale_fx
    app3.dependency_overrides[get_market_service] = _live_service
    client3 = TestClient(app3)
    try:
        resp3 = client3.post("/api/fx/rank", json={
            "symbols": ["AAPL", "MC.PA"], "target_ccy": "USD",
            "allow_fallback": True,
        })
        assert resp3.status_code == 423, resp3.text
        assert resp3.json()["error"]["code"] == "FX_PROVENANCE_MISSING"
    finally:
        fxapi.reset_fx_provider()
        _teardown()


# --- fail-closed service assertions --------------------------------------------

def test_live_quote_and_bars_carry_fallback_false():
    """Live doubles serve with fallback_used False (the only servable shape)."""
    svc = _live_service()
    try:
        quote = svc.get_quote("AAPL")
        assert quote["price"] is not None
        assert quote["provenance"]["fallback_used"] is False
        # Direct provider live double also carries fallback False.
        prov = svc.provider
        raw = prov.get_quote("AAPL")
        assert raw["fallback_used"] is False
        assert raw.get("price") is not None
    finally:
        _teardown()


def test_outage_quote_and_bars_raise_fail_closed():
    """Stub/outage service raises ProviderError — never flagged stubs."""
    svc = _stub_service()
    try:
        try:
            svc.get_quote("AAPL")
        except ProviderError:
            pass
        else:
            raise AssertionError("get_quote must raise fail-closed on outage")
        # Bars for an unknown symbol have no DB coverage and no live fetch:
        # fail-closed raise (AAPL may be DB-served, so use an unknown name).
        try:
            svc.get_bars("ZZZNOPE123", timeframe="1d", limit=10)
        except ProviderError:
            pass
        else:
            raise AssertionError("get_bars must raise fail-closed on outage")
        # Unknown symbols also raise (never a fake 100.0 stub).
        try:
            svc.get_quote("ZZZNOPE123")
        except ProviderError:
            pass
        else:
            raise AssertionError("unknown-symbol quote must raise fail-closed")
    finally:
        _teardown()
