"""M8 failure-mode tests: outage fallback, breaker isolation, bad input, AI
fail-safe, FX gate, missing symbols. No network; all outages simulated via
stub providers / monkeypatched fetch / open breakers.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.ai.providers import base as base_module
from backend.ai.schemas import AIOpinion, parse_opinion_strict
from backend.api import ai as ai_api
from backend.api import fx as fxapi
from backend.api.backtest import reset_backtest_history
from backend.api.deps import get_market_service, reset_deps
from backend.api.main import create_app
from backend.cache import InMemoryCache
from backend.forecasting.service import reset_forecast_service
from backend.instruments.registry import InstrumentRegistry
from backend.market_data.fx.provider import FXProvider
from backend.market_data.health import ProviderHealthTracker
from backend.market_data.providers.akshare import AKShareProvider
from backend.market_data.providers.base import CircuitBreaker, ProviderError
from backend.market_data.providers.yfinance import YFinanceProvider
from backend.market_data.service import MarketDataService
from backend.security import secrets as secrets_module
from backend.security.secrets import EncryptedSecretStore


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _stub_service(provider=None, akshare_provider=None) -> MarketDataService:
    tracker = ProviderHealthTracker()
    if provider is None:
        provider = YFinanceProvider(
            stub_mode=True, on_call=lambda p, ms, ok: tracker.record(p, ms, ok)
        )
    kwargs: dict = {
        "registry": InstrumentRegistry(), "provider": provider,
        "health": tracker, "cache": InMemoryCache(),
    }
    if akshare_provider is not None:
        kwargs["akshare_provider"] = akshare_provider
    return MarketDataService(**kwargs)  # type: ignore[arg-type]


def _client(market=None, fx_provider=None) -> TestClient:
    from backend.api import deps as deps_module

    reset_deps()
    reset_forecast_service()
    reset_backtest_history()
    ai_api.reset_ai_router()
    fxapi.reset_fx_provider()
    secrets_module.reset_fernet()

    stub = market if isinstance(market, MarketDataService) else _stub_service()
    deps_module._service = stub
    deps_module._registry = stub.registry
    deps_module._health = stub.health
    base_module.set_default_secret_store(EncryptedSecretStore())

    app = create_app()
    app.dependency_overrides[get_market_service] = lambda: stub
    if fx_provider is not None:
        app.dependency_overrides[fxapi.get_fx_provider] = lambda: fx_provider
    return TestClient(app)


def _teardown() -> None:
    base_module.set_default_secret_store(None)
    secrets_module.reset_fernet()
    ai_api.reset_ai_router()
    fxapi.reset_fx_provider()
    reset_deps()
    reset_forecast_service()
    reset_backtest_history()


# --- provider outage -> flagged fallback + usable ---------------------------


def test_provider_outage_flagged_fallback_usable():
    provider = YFinanceProvider(stub_mode=False)

    def _boom(symbol: str) -> dict:
        raise ProviderError("yfinance", "simulated outage")

    provider._fetch_raw = _boom  # type: ignore[method-assign]
    svc = _stub_service(provider=provider)
    quote = svc.get_quote("AAPL")
    assert quote["price"] is not None  # page stays usable
    assert quote["provenance"]["fallback_used"] is True

    client = _client(market=svc)
    try:
        resp = client.get("/api/market_data/quote", params={"symbol": "AAPL"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["price"] is not None
        assert body["provenance"]["fallback_used"] is True
    finally:
        _teardown()


def test_breaker_open_returns_flagged_stub_usable():
    breaker = CircuitBreaker(failure_threshold=1)
    breaker.record_failure()
    assert breaker.state == "open"
    provider = YFinanceProvider(stub_mode=False, breaker=breaker)
    out = provider.get_quote("AAPL")
    assert out["fallback_used"] is True
    assert out.get("circuit_open") is True
    assert out["price"] is not None


# --- breaker isolation per provider ----------------------------------------


def test_breaker_objects_are_isolated():
    yf = YFinanceProvider(stub_mode=True, breaker=CircuitBreaker(failure_threshold=1))
    ak = AKShareProvider(stub_mode=True, breaker=CircuitBreaker(failure_threshold=1))
    fx = FXProvider(breaker=CircuitBreaker(failure_threshold=1))
    assert yf.breaker is not ak.breaker
    yf.breaker.record_failure()
    assert yf.breaker.state == "open"
    assert ak.breaker.state == "closed"  # yfinance down != akshare down
    assert fx.breaker.state == "closed"


def test_sse_chain_survives_yfinance_outage_via_akshare():
    """yfinance breaker open: SSE quote still served live via AKShare."""
    yf_breaker = CircuitBreaker(failure_threshold=1)
    yf_breaker.record_failure()
    yf = YFinanceProvider(stub_mode=False, breaker=yf_breaker)

    def _boom(symbol: str) -> dict:
        raise ProviderError("yfinance", "yfinance down")

    yf._fetch_raw = _boom  # type: ignore[method-assign]

    ak = AKShareProvider(stub_mode=False)

    def _live(symbol: str, market=None) -> dict:
        return {
            "symbol": "600519.SS", "price": 1700.0, "open": 1690.0,
            "high": 1710.0, "low": 1685.0, "prev_close": 1692.0,
            "volume": 1_000_000, "currency": "CNY", "as_of": _utcnow(),
        }

    ak._fetch_raw = _live  # type: ignore[method-assign]
    svc = _stub_service(provider=yf, akshare_provider=ak)
    quote = svc.get_quote("600519.SS")
    assert quote["price"] == 1700.0
    assert quote["currency"] == "CNY"
    assert quote["provenance"]["fallback_used"] is False
    assert quote["provenance"]["source"] == "akshare"
    assert yf.breaker.state == "open" and ak.breaker.state == "closed"


# --- invalid horizon -> 422 -------------------------------------------------


def test_invalid_horizon_rejected_with_422():
    client = _client()
    try:
        for bad in ("7", "7d", "30", "soon"):
            resp = client.get("/api/forecast/AAPL", params={"horizon": bad})
            assert resp.status_code == 422, (bad, resp.text)
        assert client.get("/api/forecast/AAPL", params={"horizon": 7}).status_code == 422
        resp = client.post("/api/backtest/run", json={"symbol": "AAPL", "horizons": [7]})
        assert resp.status_code == 422, resp.text
        resp = client.post(
            "/api/ai/forecast_opinion", json={"symbol": "AAPL", "horizon": 7}
        )
        assert resp.status_code == 422, resp.text
        resp = client.post("/api/ai/insight", json={"symbol": "AAPL", "profile": "nope"})
        assert resp.status_code == 422, resp.text
    finally:
        _teardown()


# --- malformed AI JSON fails safe -------------------------------------------


def test_malformed_ai_json_fails_safe():
    for bad in ["", "   ", "{not json", '{"direction": "bullish"', "[]", "null"]:
        with pytest.raises(ValueError):
            parse_opinion_strict(bad)
    # Right shape, wrong values -> safe failure, never a partial opinion.
    with pytest.raises(ValueError):
        parse_opinion_strict('{"direction": "bullish", "probability": 9.9}')
    with pytest.raises(ValueError):
        AIOpinion.model_validate({
            "direction": "bullish", "probability": 0.5, "time_horizon_days": 7,
            "catalysts": [], "risks": [],
            "evidence_ids": ["ev-1"], "limitations": ["x"],
        })


# --- FX stale -> rank 423 ----------------------------------------------------


class _StaleFX(FXProvider):
    def get_rate(self, base: str, quote: str) -> dict:
        out = super().get_rate(base, quote)
        out["as_of"] = _utcnow() - timedelta(hours=30)
        out["fallback_used"] = False
        return out


def test_fx_stale_rank_423():
    client = _client(fx_provider=_StaleFX(stub_mode=True))
    try:
        resp = client.post(
            "/api/fx/rank",
            json={"symbols": ["AAPL", "MC.PA"], "target_ccy": "USD",
                  "allow_fallback": True},
        )
        assert resp.status_code == 423, resp.text
        assert resp.json()["error"]["code"] == "FX_PROVENANCE_MISSING"
    finally:
        _teardown()


def test_fx_fallback_without_flag_rank_423():
    client = _client(fx_provider=FXProvider(stub_mode=True))  # stub = fallback
    try:
        resp = client.post(
            "/api/fx/rank",
            json={"symbols": ["AAPL", "MC.PA"], "target_ccy": "USD"},
        )
        assert resp.status_code == 423, resp.text
        assert resp.json()["error"]["code"] == "FX_PROVENANCE_MISSING"
        ok = client.post(
            "/api/fx/rank",
            json={"symbols": ["AAPL", "MC.PA"], "target_ccy": "USD",
                  "allow_fallback": True},
        )
        assert ok.status_code == 200, ok.text
    finally:
        _teardown()


# --- missing symbol -> 404 / empty, never 500 -------------------------------


def test_missing_symbol_404_or_empty_not_500():
    client = _client()
    try:
        resp = client.get("/api/instruments/resolve", params={"symbol": "ZZZ_NOPE_123"})
        assert resp.status_code == 404, resp.text

        resp = client.get("/api/instruments/does-not-exist-123")
        assert resp.status_code == 404, resp.text

        resp = client.get("/api/securities/does-not-exist-123/quote")
        assert resp.status_code == 404, resp.text

        # Unknown symbols in logs/history return empty payloads, not 500.
        resp = client.get("/api/audit/forecasts", params={"symbol": "ZZZ_NOPE_123"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["count"] == 0

        resp = client.get("/api/backtest/ZZZ_NOPE_123")
        assert resp.status_code == 200, resp.text
        assert resp.json()["runs"] == []

        # Empty symbol is a client error (422), never a 500.
        resp = client.get("/api/market_data/quote", params={"symbol": ""})
        assert resp.status_code in (404, 422), resp.text
        assert resp.status_code != 500
    finally:
        _teardown()
