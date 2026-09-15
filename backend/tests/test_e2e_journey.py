"""M8 E2E journey: Search -> Quote -> Forecast -> Analytics -> Backtest -> AI -> Audit.

Full-stack walk through ``backend.api.main.create_app`` with TestClient.
No network: market data uses live doubles (deterministic stub quotes
marked live: fallback_used=False, price present) injected into both the
FastAPI dependency graph AND the ``deps`` singleton (AI packet building
reads the singleton directly). Bars come from the seeded DB (live
provenance, fallback_used False). AI uses an empty secret store so
opinion calls without keys take the fail-closed 423 path (deterministic
forecasting is unaffected — DoD 7).

Asserts: every deterministic step 200, full provenance envelope where the
contract requires it, "Not investment advice" disclosure on forecast +
audit + AI outputs, AI-disabled blend passes quant through intact.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.ai.providers import base as base_module
from backend.api import ai as ai_api
from backend.api.deps import get_market_service, reset_deps
from backend.api.main import create_app
from backend.api.backtest import reset_backtest_history
from backend.cache import InMemoryCache
from backend.forecasting.service import reset_forecast_service
from backend.instruments.registry import InstrumentRegistry
from backend.market_data.health import ProviderHealthTracker
from backend.market_data.providers.yfinance import YFinanceProvider
from backend.market_data.service import MarketDataService
from backend.security import secrets as secrets_module
from backend.security.secrets import EncryptedSecretStore

REQUIRED_PROVENANCE = {
    "source", "as_of", "delay_minutes",
    "quality_grade", "fallback_used", "missing_fields",
}


def _stub_service() -> MarketDataService:
    """Live double: stub quotes marked live (fail-closed contract)."""
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
    return MarketDataService(
        registry=InstrumentRegistry(), provider=provider,
        health=tracker, cache=InMemoryCache(),
    )


def _client() -> TestClient:
    """Full app with live market data (no network) + no-key AI (423 path)."""
    from backend.api import deps as deps_module

    reset_deps()
    reset_forecast_service()
    reset_backtest_history()
    ai_api.reset_ai_router()
    secrets_module.reset_fernet()

    stub = _stub_service()
    # AI packet building calls get_market_service() directly (not via
    # Depends), so seed the singleton as well as the override.
    deps_module._service = stub
    deps_module._registry = stub.registry
    deps_module._health = stub.health

    empty_store = EncryptedSecretStore()
    base_module.set_default_secret_store(empty_store)

    app = create_app()
    app.dependency_overrides[get_market_service] = lambda: stub
    client = TestClient(app)
    client._empty_store = empty_store  # type: ignore[attr-defined]
    return client


def _teardown() -> None:
    base_module.set_default_secret_store(None)
    secrets_module.reset_fernet()
    ai_api.reset_ai_router()
    reset_deps()
    reset_forecast_service()
    reset_backtest_history()


def _assert_provenance(body: dict, where: str) -> None:
    prov = body.get("provenance")
    assert isinstance(prov, dict), f"{where}: missing provenance envelope"
    missing = REQUIRED_PROVENANCE - set(prov)
    assert not missing, f"{where}: provenance missing {missing}"


def test_e2e_journey_search_to_audit():
    client = _client()
    try:
        # 1. Search (unified, exchange-aware).
        resp = client.get("/api/instruments/search", params={"q": "AAPL"})
        assert resp.status_code == 200, resp.text
        search = resp.json()
        assert search["results"], "search returned no results for AAPL"
        assert search["results"][0]["exchange_symbol"] == "AAPL"
        _assert_provenance(search, "search")

        # 2. Quote (live only).
        resp = client.get("/api/market_data/quote", params={"symbol": "AAPL"})
        assert resp.status_code == 200, resp.text
        quote = resp.json()
        assert quote["symbol"] == "AAPL"
        assert quote["price"] is not None  # live number, never a stub fallback
        assert quote["currency"] == "USD"
        _assert_provenance(quote, "quote")
        assert quote["provenance"]["fallback_used"] is False

        # 3. Forecast (deterministic core, no AI).
        resp = client.get("/api/forecast/AAPL", params={"horizon": 21})
        assert resp.status_code == 200, resp.text
        forecast = resp.json()
        assert forecast["symbol"] == "AAPL"
        assert forecast["horizon_days"] == 21
        assert 0.0 <= forecast["direction_probability"] <= 1.0
        assert forecast["model_version"] and forecast["feature_version"]
        assert forecast["data_version"] and forecast["as_of"]
        assert forecast["disclosure"] == "Not investment advice"
        _assert_provenance(forecast, "forecast")

        # 4. Analytics bundle.
        resp = client.get("/api/analytics/AAPL")
        assert resp.status_code == 200, resp.text
        analytics = resp.json()
        assert analytics["symbol"] == "AAPL"
        for section in ("technical", "fundamentals", "quality", "valuation"):
            assert isinstance(analytics.get(section), dict) and analytics[section]
        _assert_provenance(analytics, "analytics")

        # 5. Backtest run + history.
        resp = client.post(
            "/api/backtest/run",
            json={"symbol": "AAPL", "horizons": [5, 21],
                  "train_size": 100, "test_size": 21, "gap": 21},
        )
        assert resp.status_code == 200, resp.text
        run = resp.json()
        assert run["symbol"] == "AAPL"
        assert set(run["results"]) == {"5", "21"}
        assert run["model_version"] and run["feature_version"] and run["data_version"]
        _assert_provenance(run, "backtest-run")
        resp = client.get("/api/backtest/AAPL")
        assert resp.status_code == 200, resp.text
        hist = resp.json()
        assert hist["symbol"] == "AAPL"
        assert len(hist["runs"]) == 1
        _assert_provenance(hist, "backtest-history")

        # 6. AI insight without keys -> 423 (fail-closed, never a stub opinion).
        resp = client.post(
            "/api/ai/insight",
            json={"symbol": "AAPL", "profile": "quick_insight"},
        )
        assert resp.status_code == 423, resp.text
        assert "no API key" in resp.text or "AI disabled" in resp.text

        # AI-disabled blend preview: quant core passes through intact.
        resp = client.post(
            "/api/ai/forecast_opinion",
            json={"symbol": "AAPL", "horizon": 21, "quant_prob": 0.64,
                  "ai_weight": 0.2, "ai_enabled": False},
        )
        assert resp.status_code == 200, resp.text
        blend_body = resp.json()
        assert blend_body["blend"]["blended_prob"] == 0.64
        assert blend_body["blend"]["ai_applied"] is False
        assert blend_body["ai_weight_applied"] == 0.0
        assert "Not investment advice" in blend_body["disclaimer"]

        # 7. Audit logs (empty on a fresh DB is fine; never 500).
        resp = client.get("/api/audit/forecasts", params={"symbol": "AAPL"})
        assert resp.status_code == 200, resp.text
        audits = resp.json()
        assert "forecasts" in audits and "count" in audits
        assert "Not investment advice" in audits["disclosure"]
        resp = client.get("/api/audit/ai_decisions")
        assert resp.status_code == 200, resp.text
        assert "decisions" in resp.json()
    finally:
        _teardown()


def test_e2e_ai_disabled_leaves_forecast_intact():
    """DoD 7: disabling AI leaves the deterministic forecast untouched."""
    client = _client()
    try:
        before_resp = client.get("/api/forecast/MSFT", params={"horizon": 21})
        assert before_resp.status_code == 200, before_resp.text
        before = before_resp.json()
        disabled = client.post(
            "/api/ai/forecast_opinion",
            json={"symbol": "MSFT", "horizon": 21, "quant_prob": 0.64,
                  "ai_enabled": False},
        )
        assert disabled.status_code == 200, disabled.text
        disabled_body = disabled.json()
        assert disabled_body["blend"]["blended_prob"] == 0.64
        assert disabled_body["blend"]["ai_applied"] is False
        assert disabled_body["ai_weight_applied"] == 0.0
        after_resp = client.get("/api/forecast/MSFT", params={"horizon": 21})
        assert after_resp.status_code == 200, after_resp.text
        after = after_resp.json()
        assert after["direction_probability"] == before["direction_probability"]
        assert after["model_version"] == before["model_version"]
        # Deterministic engine carries ai_weight 0 (no AI influence).
        assert after.get("record", {}).get("ai_weight", 0) == 0
    finally:
        _teardown()
