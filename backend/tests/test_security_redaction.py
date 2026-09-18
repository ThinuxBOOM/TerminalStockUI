"""M8 security/redaction tests: keys never reach API responses or logs.

Covers: Fernet round-trip + wrong-key failure, EncryptedSecretStore
ciphertext-at-rest + safe describe(), redacted logs (mapping + string),
and an end-to-end scan of insight / forecast / audit JSON for planted
``sk-`` secrets. Fail-closed paths (423 AI-disabled, 502 outage) must
also be key-free.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.ai.providers import base as base_module
from backend.api import ai as ai_api
from backend.api.deps import get_market_service, reset_deps
from backend.api.main import create_app
from backend.cache import InMemoryCache
from backend.forecasting.service import reset_forecast_service
from backend.instruments.registry import InstrumentRegistry
from backend.market_data.health import ProviderHealthTracker
from backend.market_data.providers.yfinance import YFinanceProvider
from backend.market_data.service import MarketDataService
from backend.security import secrets as secrets_module
from backend.security.secrets import (
    EncryptedSecretStore,
    decrypt_secret,
    encrypt_secret,
    redact_mapping,
    redact_string,
    reset_fernet,
)


@pytest.fixture()
def _fresh_key(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-only-secret-key-for-m8-redaction")
    reset_fernet()
    yield
    reset_fernet()


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


def _client_with_store(store: EncryptedSecretStore) -> TestClient:
    from backend.api import deps as deps_module
    from backend.tests.auth_helpers import inject_admin_auth

    reset_deps()
    reset_forecast_service()
    ai_api.reset_ai_router()
    stub = _stub_service()
    deps_module._service = stub
    deps_module._registry = stub.registry
    deps_module._health = stub.health
    base_module.set_default_secret_store(store)
    app = create_app()
    app.dependency_overrides[get_market_service] = lambda: stub
    inject_admin_auth(app)
    return TestClient(app)


def test_fernet_round_trip(_fresh_key):
    token = encrypt_secret("sk-gemini-m8-secret")
    assert token != "sk-gemini-m8-secret"
    assert decrypt_secret(token) == "sk-gemini-m8-secret"


def test_fernet_wrong_key_fails_safe(monkeypatch, tmp_path):
    monkeypatch.setenv("SECRET_KEY", "key-one-for-m8-test-only-00000001")
    reset_fernet()
    token = encrypt_secret("sk-rotate-me")
    monkeypatch.setenv("SECRET_KEY", "key-two-for-m8-test-only-00000002")
    reset_fernet()
    with pytest.raises(ValueError):
        decrypt_secret(token)
    reset_fernet()


def test_store_encrypts_at_rest_and_describes_safely(_fresh_key):
    store = EncryptedSecretStore()
    store.put("gemini", "api_key", "sk-live-m8-gemini")
    assert store._vault[("gemini", "api_key")] != "sk-live-m8-gemini"
    assert store.get("gemini", "api_key") == "sk-live-m8-gemini"
    described = store.describe()
    assert described == [{"provider": "gemini", "name": "api_key", "configured": True}]
    assert "sk-live-m8-gemini" not in str(described)


def test_redacted_logs(_fresh_key):
    secret = "sk-live-m8-log-secret"
    assert secret not in redact_string(f"call failed api_key={secret} retrying")
    assert secret not in redact_string(f"Authorization={secret}")
    assert secret not in redact_string(f"token: {secret}")
    payload = {
        "actor": "user:1", "api_key": secret,
        "nested": {"token": "tok-m8", "ok": 1},
        "items": [{"password": "pw-m8"}],
    }
    clean = redact_mapping(payload)
    assert clean["api_key"] == "[REDACTED]"
    assert clean["nested"]["token"] == "[REDACTED]"
    assert clean["nested"]["ok"] == 1
    assert secret not in str(clean) and "tok-m8" not in str(clean)


def test_keys_never_in_api_responses(_fresh_key):
    """Plant live-looking keys, then scan 200/423/502 JSON for leaks."""
    store = EncryptedSecretStore()
    gemini_secret = "sk-live-m8-gemini-abc123"
    openai_secret = "sk-live-m8-openai-xyz789"
    store.put("openai", "api_key", openai_secret)
    # NOTE: gemini key is planted only for the health/describe scan below.
    # The insight call itself uses the openai-key-only store so the gemini
    # profile has no key and takes the fail-closed 423 path (never a stub
    # opinion, never a live HTTPS call).
    client = _client_with_store(store)
    try:
        # Insight without a gemini key -> 423, key-free (fail-closed).
        resp = client.post(
            "/api/ai/insight", json={"symbol": "AAPL", "profile": "quick_insight"}
        )
        assert resp.status_code == 423, resp.text
        for secret in (openai_secret, gemini_secret):
            assert secret not in resp.text
        assert "sk-live" not in resp.text

        # AI forecast_opinion without keys but ai_enabled=True -> 423, key-free.
        resp = client.post(
            "/api/ai/forecast_opinion",
            json={"symbol": "AAPL", "horizon": 21, "quant_prob": 0.6},
        )
        assert resp.status_code == 423, resp.text
        assert openai_secret not in resp.text
        assert gemini_secret not in resp.text
        assert "sk-live" not in resp.text

        # AI-disabled blend still works without keys (deterministic intact).
        resp = client.post(
            "/api/ai/forecast_opinion",
            json={"symbol": "AAPL", "horizon": 21, "quant_prob": 0.6,
                  "ai_enabled": False},
        )
        assert resp.status_code == 200, resp.text
        assert openai_secret not in resp.text
        assert gemini_secret not in resp.text

        # Forecast + audit surfaces never carry key material.
        for path, kwargs in [
            ("/api/forecast/AAPL", {"params": {"horizon": 21}}),
            ("/api/audit/forecasts", {"params": {"symbol": "AAPL"}}),
            ("/api/audit/ai_decisions", {}),
            ("/api/ai/providers/performance", {}),
        ]:
            r = client.get(path, **kwargs)
            assert r.status_code == 200, (path, r.text)
            assert openai_secret not in r.text
            assert gemini_secret not in r.text  # never stored, never returned

        # 502 outage path (unknown symbol has no live bars) is also key-free.
        r = client.get("/api/forecast/ZZZ_NOPE_123", params={"horizon": 21})
        assert r.status_code in (404, 422, 502), r.text
        assert openai_secret not in r.text
        assert gemini_secret not in r.text
        assert "sk-live" not in r.text

        # Health/describe path with a gemini key configured: names only.
        store.put("gemini", "api_key", gemini_secret)
        ai_api.reset_ai_router()
        r = client.post("/api/ai/providers/health/test", json={})
        assert r.status_code == 200, r.text
        assert gemini_secret not in r.text
        assert openai_secret not in r.text
        assert "sk-live" not in r.text  # no live-looking secret prefix anywhere
    finally:
        base_module.set_default_secret_store(None)
        ai_api.reset_ai_router()
        reset_deps()
        reset_forecast_service()
        reset_fernet()
