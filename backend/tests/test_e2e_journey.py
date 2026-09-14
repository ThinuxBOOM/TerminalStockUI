"""M8 E2E journey: Search -> Quote -> Forecast -> Analytics -> Backtest -> AI (stub) -> Audit.

Full-stack walk through ``backend.api.main.create_app`` with TestClient.
No network: market data uses a stub-mode YFinanceProvider injected into both
the FastAPI dependency graph AND the ``deps`` singleton (AI packet building
reads the singleton directly). AI uses an empty secret store so insight calls
take the marked-stub path (DoD 7: app runs with AI completely disabled).

Asserts: every step 200, full provenance envelope where the contract
requires it, and "Not investment advice" disclosure on forecast + audit +
AI outputs.
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
    tracker = ProviderHealthTracker()
    provider = YFinanceProvider(
        stub_mode=True, on_call=lambda p, ms, ok: tracker.record(p, ms, ok)
    )
    return MarketDataService(
        registry=InstrumentRegistry(), provider=provider,
        health=tracker, cache=InMemoryCache(),
    )


def _client() -> TestClient:
    """Full app with stub market data (no network) + stub AI (no keys)."""
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

        # 2. Quote.
        resp = client.get("/api/market_data/quote", params={"symbol": "AAPL"})
        assert resp.status_code == 200, resp.text
        quote = resp.json()
        assert quote["symbol"] == "AAPL"
        assert quote["price"] is not None  # usable number, even on fallback
        assert quote["currency"] == "USD"
        _assert_provenance(quote, "quote")

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

        # 6. AI insight (no keys -> marked stub, never crash; DoD 7).
        resp = client.post(
            "/api/ai/insight",
            json={"symbol": "AAPL", "profile": "quick_insight"},
        )
        assert resp.status_code == 200, resp.text
        insight = resp.json()
        assert insight["symbol"] == "AAPL"
        assert insight["opinion"]["stub"] is True
        assert insight["opinion"]["evidence_ids"]
        assert "Not investment advice" in insight["disclaimer"]
        assert insight["evidence_hash"] and insight["packet_id"]
        assert isinstance(insight.get("provenance"), dict)

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
        before = client.get("/api/forecast/MSFT", params={"horizon": 21}).json()
        disabled = client.post(
            "/api/ai/forecast_opinion",
            json={"symbol": "MSFT", "horizon": 21, "quant_prob": 0.64,
                  "ai_enabled": False},
        )
        assert disabled.status_code == 200, disabled.text
        assert disabled.json()["blend"]["blended_prob"] == 0.64
        after = client.get("/api/forecast/MSFT", params={"horizon": 21}).json()
        assert after["direction_probability"] == before["direction_probability"]
        assert after["model_version"] == before["model_version"]
    finally:
        _teardown()
