"""Screener API tests: TestClient, no network, deterministic.

Covers the Phase 3a contract for GET /api/screener: response shape,
ranking order, min_direction filter, horizon validation (422), unknown
market handling (422), per-symbol failure degrades to skipped (200
overall), and the empty-universe edge.

Fail-closed contract: MarketDataService serves ONLY live data (quote:
fallback_used falsy, price present) else raises ProviderError. Tests
build live doubles by taking the deterministic stub quotes and marking
them live (fallback_used=False, pop fallback) so the service grades them
as live. Per-symbol failures still degrade to skipped[] rows; all-fail
batches return empty results with skipped reasons (200), never synthetic
rows.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import deps as deps_module
from backend.api.deps import get_market_service, get_registry, reset_deps
from backend.api.screener import router
from backend.cache import InMemoryCache
from backend.forecasting.service import reset_forecast_service
from backend.instruments.registry import InstrumentRegistry
from backend.market_data.health import ProviderHealthTracker
from backend.market_data.providers.yfinance import YFinanceProvider
from backend.market_data.service import MarketDataService

REQUIRED_PROVENANCE = {
    "source", "as_of", "delay_minutes",
    "quality_grade", "fallback_used", "missing_fields",
}

REQUIRED_ROW_KEYS = {
    "symbol", "company_name", "exchange_mic", "currency", "price",
    "change_pct", "market_state", "direction_probability", "confidence",
    "model_version", "horizons", "provenance",
}


def _stub_service() -> MarketDataService:
    """Live double: stub quotes marked live (fail-closed contract).

    Takes the deterministic YFinanceProvider stub quotes (offline,
    no network), sets fallback_used=False, pops the fallback marker and
    ensures a price is present. The service then grades them as live
    (provenance fallback_used False). Bars come from the seeded DB
    (500+ rows per symbol) so forecasts are deterministic.
    """
    tracker = ProviderHealthTracker()
    provider = YFinanceProvider(
        stub_mode=True, on_call=lambda p, ms, ok: tracker.record(p, ms, ok)
    )
    _orig_get_quote = provider.get_quote

    def _live_get_quote(symbol: str, *args, **kwargs) -> dict:  # type: ignore[no-untyped-def]
        upper = (symbol or "").strip().upper() if isinstance(symbol, str) else ""
        if not upper:
            # Preserve empty-symbol contract (ProviderError, never a stub).
            return _orig_get_quote(symbol, *args, **kwargs)
        q = _orig_get_quote(symbol, *args, **kwargs)
        q = dict(q)
        q["fallback_used"] = False
        q.pop("fallback", None)
        q.pop("circuit_open", None)
        if q.get("price") is None:
            q["price"] = 150.0
        return q

    provider.get_quote = _live_get_quote  # type: ignore[method-assign]
    return MarketDataService(
        registry=InstrumentRegistry(), provider=provider,
        health=tracker, cache=InMemoryCache(),
    )


def _clear_screener_cache() -> None:
    """Screener caches ranked envelopes in the global cache singleton.

    The key covers (market, horizon, min_direction, limit, offset) only, so
    tests that mutate the universe or stub failures must start cold or they
    read a previous test's envelope (empty-universe sees rows, flaky-AAPL
    sees no skipped). Best-effort: never raises.
    """
    try:
        from backend.cache import get_cache

        _c = get_cache()
        _clear = getattr(_c, "clear", None)
        if callable(_clear):
            _clear()
    except Exception:
        pass


def _client(svc: MarketDataService | None = None,
            registry: InstrumentRegistry | None = None) -> TestClient:
    reset_deps()
    reset_forecast_service()
    _clear_screener_cache()
    stub = svc or _stub_service()
    reg = registry or stub.registry
    deps_module._service = stub
    deps_module._registry = reg
    deps_module._health = stub.health
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_market_service] = lambda: stub
    app.dependency_overrides[get_registry] = lambda: reg
    return TestClient(app)


def _teardown() -> None:
    reset_deps()
    reset_forecast_service()
    _clear_screener_cache()


def test_screener_contract_shape_keys():
    client = _client()
    try:
        resp = client.get("/api/screener")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        for key in ("results", "count", "universe_size", "skipped", "disclosure"):
            assert key in body, f"missing top-level key {key}"
        assert body["universe_size"] == len(InstrumentRegistry().all())
        assert body["count"] == len(body["results"])
        assert isinstance(body["skipped"], list)
        assert str(body["disclosure"]).startswith("Not investment advice")
        assert body["results"], "expected non-empty default scan"
        for row in body["results"]:
            missing = REQUIRED_ROW_KEYS - set(row)
            assert not missing, f"row missing {missing}"
            assert 0.0 <= row["direction_probability"] <= 1.0
            assert row["model_version"]
            assert row["horizons"] == [21]
            assert REQUIRED_PROVENANCE - set(row["provenance"]) == set()
            # Fail-closed: screener rows are live only, never fallback stubs.
            assert row["provenance"]["fallback_used"] is False
            assert row["price"] is not None
    finally:
        _teardown()


def test_screener_ranking_order_desc():
    client = _client()
    try:
        body = client.get("/api/screener", params={"limit": 50}).json()
        probs = [r["direction_probability"] for r in body["results"]]
        assert probs == sorted(probs, reverse=True)
    finally:
        _teardown()


def test_screener_min_direction_filter():
    client = _client()
    try:
        loose = client.get("/api/screener", params={"min_direction": 0.0, "limit": 50})
        assert loose.status_code == 200, loose.text
        strict = client.get("/api/screener", params={"min_direction": 0.99, "limit": 50})
        assert strict.status_code == 200, strict.text
        loose_body, strict_body = loose.json(), strict.json()
        assert loose_body["count"] >= strict_body["count"]
        for row in strict_body["results"]:
            assert row["direction_probability"] >= 0.99
        # Out-of-range thresholds are rejected, not clamped.
        assert client.get("/api/screener", params={"min_direction": 1.5}).status_code == 422
        assert client.get("/api/screener", params={"min_direction": -0.1}).status_code == 422
    finally:
        _teardown()


def test_screener_horizon_validation():
    client = _client()
    try:
        for horizon in (5, 21, 63):
            resp = client.get("/api/screener", params={"horizon": horizon})
            assert resp.status_code == 200, (horizon, resp.text)
            assert resp.json()["horizon"] == horizon
        for bad in (1, 7, 10, 30, 126):
            resp = client.get("/api/screener", params={"horizon": bad})
            assert resp.status_code == 422, (bad, resp.text)
    finally:
        _teardown()


def test_screener_unknown_market_handling():
    client = _client()
    try:
        resp = client.get("/api/screener", params={"market": "XXXX"})
        assert resp.status_code == 422, resp.text
        for mic in ("XNAS", "XNYS", "XSHG", "XPAR", "XAMS", "XBRU"):
            scoped = client.get("/api/screener", params={"market": mic, "limit": 50})
            assert scoped.status_code == 200, (mic, scoped.text)
            body = scoped.json()
            assert body["universe_size"] > 0
            assert all(r["exchange_mic"] == mic for r in body["results"])
        all_resp = client.get("/api/screener", params={"market": "ALL", "limit": 50})
        assert all_resp.status_code == 200, all_resp.text
        assert all_resp.json()["universe_size"] == len(InstrumentRegistry().all())
    finally:
        _teardown()


def test_screener_per_symbol_failure_degrades():
    stub = _stub_service()
    real_get_quote = stub.get_quote

    def _flaky(symbol: str, market: str | None = None) -> dict:
        if str(symbol).upper() == "AAPL":
            raise RuntimeError("simulated provider outage for AAPL")
        return real_get_quote(symbol, market)

    stub.get_quote = _flaky  # type: ignore[method-assign]
    client = _client(svc=stub)
    try:
        resp = client.get("/api/screener", params={"min_direction": 0.0, "limit": 50})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert any(s["symbol"] == "AAPL" and s["reason"] for s in body["skipped"])
        assert all(r["symbol"] != "AAPL" for r in body["results"])
    finally:
        _teardown()


def test_screener_all_fail_returns_empty_with_skipped():
    """Fail-closed: all-fail batch is 200 with empty results + skipped reasons.

    Never synthetic rows. Per-symbol degrade (backend/api/screener.py
    _scan_one) collects {symbol, reason} per failure; the batch envelope
    stays honest.
    """
    from backend.market_data.providers.base import ProviderError

    stub = _stub_service()

    def _boom(symbol: str, market: str | None = None) -> dict:
        raise ProviderError("yfinance", f"simulated outage for {symbol}")

    stub.get_quote = _boom  # type: ignore[method-assign]
    client = _client(svc=stub)
    try:
        resp = client.get("/api/screener", params={"min_direction": 0.0, "limit": 50})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["results"] == []
        assert body["count"] == 0
        assert body["universe_size"] == len(InstrumentRegistry().all())
        assert len(body["skipped"]) == body["universe_size"]
        for entry in body["skipped"]:
            assert entry.get("symbol") and entry.get("reason")
        # Honest provenance for an empty scan: non-fallback envelope.
        assert body["provenance"]["fallback_used"] is False
    finally:
        _teardown()


def test_screener_empty_universe_edge():
    # Note: InstrumentRegistry([]) falls back to seeds by design (empty
    # list is falsy), so clear an instance to simulate an empty universe.
    empty = InstrumentRegistry()
    empty._items = []
    client = _client(registry=empty)
    try:
        resp = client.get("/api/screener")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["results"] == []
        assert body["count"] == 0
        assert body["universe_size"] == 0
        assert body["skipped"] == []
    finally:
        _teardown()


def test_screener_limit_cap():
    client = _client()
    try:
        assert client.get("/api/screener", params={"limit": 51}).status_code == 422
        resp = client.get(
            "/api/screener", params={"min_direction": 0.0, "limit": 5}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["count"] <= 5
    finally:
        _teardown()
