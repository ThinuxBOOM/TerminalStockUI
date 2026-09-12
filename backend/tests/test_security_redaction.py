"""M8 security/redaction tests: keys never reach API responses or logs.

Covers: Fernet round-trip + wrong-key failure, EncryptedSecretStore
ciphertext-at-rest + safe describe(), redacted logs (mapping + string),
and an end-to-end scan of insight / forecast / audit JSON for planted
``sk-`` secrets.
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
    tracker = ProviderHealthTracker()
    provider = YFinanceProvider(
        stub_mode=True, on_call=lambda p, ms, ok: tracker.record(p, ms, ok)
    )
    return MarketDataService(
        registry=InstrumentRegistry(), provider=provider,
        health=tracker, cache=InMemoryCache(),
    )


def _client_with_store(store: EncryptedSecretStore) -> TestClient:
    from backend.api import deps as deps_module

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
    """Plant live-looking keys, then scan insight/forecast/audit JSON."""
    store = EncryptedSecretStore()
    gemini_secret = "sk-live-m8-gemini-abc123"
    openai_secret = "sk-live-m8-openai-xyz789"
    store.put("openai", "api_key", openai_secret)
    # NOTE: gemini key is planted only for the health/describe scan below.
    # The insight call itself uses the openai-key-only store so it stays on
    # the fast no-key stub path (a configured gemini key would attempt a
    # live HTTPS call first).
    client = _client_with_store(store)
    try:
        # Insight (gemini stub) must not leak the planted openai key.
        resp = client.post(
            "/api/ai/insight", json={"symbol": "AAPL", "profile": "quick_insight"}
        )
        assert resp.status_code == 200, resp.text
        for secret in (openai_secret,):
            assert secret not in resp.text

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
