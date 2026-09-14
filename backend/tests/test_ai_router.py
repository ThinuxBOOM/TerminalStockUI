"""AI router / providers / API tests (spec section 6 + M4/M5 acceptance).

Covers: profile map defaults to Gemini Flash, evidence-hash caching, token
logging, performance tracker by exchange+horizon, stub-when-no-key for all
vendors (never crash), key redaction, and the /api/ai endpoints.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.ai.blend import AI_WEIGHT_MAX
from backend.ai.evidence import build_evidence_packet
from backend.ai.prompts import PROFILES, get_prompt
from backend.ai.providers.anthropic import AnthropicProvider
from backend.ai.providers.base import BaseProvider, get_default_secret_store
from backend.ai.providers.gemini import GeminiProvider
from backend.ai.providers.openai import OpenAIProvider
from backend.ai.providers.xai import XAIProvider
from backend.ai.router import AIRouter, DEFAULT_PROFILE_MAP
from backend.ai.schemas import AIOpinion
from backend.api import ai as ai_api
from backend.security.secrets import EncryptedSecretStore, redact_mapping


@pytest.fixture()
def empty_store(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-only-secret-key-for-ai-router-tests")
    # Hermetic: provider is_configured() falls back to <PROVIDER>_API_KEY env
    # and DB rows. Clear both so "no key" really means stub for all vendors.
    for _var in ("GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "XAI_API_KEY"):
        monkeypatch.delenv(_var, raising=False)
    try:
        from backend.security import store_db as store_db_module

        monkeypatch.setattr(store_db_module, "get_db_secret", lambda provider: None)
    except Exception:
        pass
    from backend.security import secrets as secrets_module

    secrets_module.reset_fernet()
    store = EncryptedSecretStore()
    from backend.ai.providers import base as base_module

    base_module.set_default_secret_store(store)
    ai_api.reset_ai_router()
    yield store
    base_module.set_default_secret_store(None)
    secrets_module.reset_fernet()
    ai_api.reset_ai_router()


def _packet(symbol: str = "AAPL"):
    return build_evidence_packet(
        symbol,
        {
            "top_bullish": [{"label": "trend support", "detail": "price above SMA50"}],
            "top_risks": [{"label": "valuation stretch"}],
            "events": [{"label": "earnings next week"}],
            "summary": {"rsi": 55},
        },
        {"source": "yfinance", "quality_grade": "B", "delay_minutes": 15},
        instrument={"instrument_id": "US0378331005", "exchange_mic": "XNAS", "currency": "USD"},
    )


def test_profile_map_defaults_to_gemini_flash():
    assert set(PROFILES) == {"quick_insight", "forecast_assist", "deep_research", "report"}
    for profile, (provider, model) in DEFAULT_PROFILE_MAP.items():
        assert provider == "gemini"
        assert "flash" in model
    router = AIRouter(secret_store=EncryptedSecretStore())
    for profile in PROFILES:
        assert router.resolve(profile) == ("gemini", "gemini-3.7-flash")
    with pytest.raises(ValueError):
        router.resolve("nope")


def test_prompts_bounded_and_disclosed():
    for profile in PROFILES:
        text = get_prompt(profile).lower()
        assert "strict json" in text
        assert "evidence_ids" in text or "evidence ids" in text
        assert "not investment advice" in text


def test_all_providers_stub_without_key(empty_store):
    packet = _packet()
    for cls in (GeminiProvider, OpenAIProvider, AnthropicProvider, XAIProvider):
        provider = cls(secret_store=empty_store)
        assert provider.health()["configured"] is False
        opinion = asyncio.run(provider.insight(packet, profile="quick_insight"))
        assert isinstance(opinion, AIOpinion)
        assert opinion.stub is True  # marked stub, never crash
        assert opinion.limitations  # disclosure present
        dumped = opinion.model_dump_json()
        assert "api_key" not in dumped.lower()  # never key material in output
        assert "Authorization" not in dumped


def test_provider_health_never_returns_key(empty_store):
    empty_store.put("gemini", "api_key", "sk-live-abc123")
    provider = GeminiProvider(secret_store=empty_store)
    health = redact_mapping(provider.health())
    assert health["configured"] is True
    assert "sk-live-abc123" not in str(health)


def test_router_cache_by_evidence_hash(empty_store):
    calls = {"n": 0}

    class CountingProvider(BaseProvider):
        name = "gemini"
        default_model = "gemini-3.7-flash"

        async def insight(self, packet, *, profile="quick_insight", horizon=None):
            calls["n"] += 1
            return AIOpinion(
                direction="neutral", probability=0.5,
                time_horizon_days=horizon or 21,
                catalysts=["trend"], risks=["valuation"],
                evidence_ids=packet.evidence_ids or ["ev-1"],
                limitations=["Not investment advice."],
                provider=self.name, model=self.model,
            )

    router = AIRouter(providers={"gemini": CountingProvider(secret_store=empty_store)})
    packet = _packet()
    first, cached_first = asyncio.run(router.get_insight(packet, profile="quick_insight"))
    second, cached_second = asyncio.run(router.get_insight(packet, profile="quick_insight"))
    assert cached_first is False
    assert cached_second is True  # same evidence-hash -> cache hit
    assert calls["n"] == 1
    assert first == second  # identical cached opinion
    assert packet.evidence_hash  # cache key derived from packet content hash
    assert router.token_log  # token usage logged on both paths
    assert all("api_key" not in str(entry) for entry in router.token_log)


def test_router_tracks_performance_by_exchange_horizon(empty_store):
    router = AIRouter(secret_store=empty_store)
    packet = _packet("AAPL")
    asyncio.run(router.get_insight(packet, profile="quick_insight", horizon=21))
    rows = router.performance.summary(exchange="XNAS", horizon=21)
    assert len(rows) == 1
    row = rows[0]
    assert row["provider"] == "gemini"
    assert row["exchange"] == "XNAS"
    assert row["horizon"] == 21
    assert row["calls"] == 1


def _client(empty_store) -> TestClient:
    app = FastAPI()
    app.include_router(ai_api.router)

    async def _no_market_dependency():
        return None  # endpoints resolve market data internally; offline stub covers it

    _ = _no_market_dependency
    return TestClient(app)


def test_api_insight_roundtrip(empty_store):
    client = _client(empty_store)
    response = client.post("/api/ai/insight", json={"symbol": "AAPL", "profile": "quick_insight"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["symbol"] == "AAPL"
    assert body["provider"] == "gemini"
    opinion = body["opinion"]
    assert opinion["time_horizon_days"] in (5, 21, 63)
    assert 0.0 <= opinion["probability"] <= 1.0
    assert opinion["evidence_ids"]
    assert "api_key" not in response.text.lower()  # never key material in API output
    # Second identical call is served from the evidence-hash cache.
    again = client.post("/api/ai/insight", json={"symbol": "AAPL", "profile": "quick_insight"})
    assert again.json()["cached"] is True
    assert again.json()["evidence_hash"] == body["evidence_hash"]


def test_api_forecast_opinion_with_blend_preview(empty_store):
    client = _client(empty_store)
    response = client.post(
        "/api/ai/forecast_opinion",
        json={"symbol": "AAPL", "horizon": 21, "quant_prob": 0.64,
              "ai_weight": 0.2, "ai_enabled": True},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["opinion"]["time_horizon_days"] == 21
    blend = body["blend"]
    assert blend["quant_prob"] == pytest.approx(0.64)
    assert abs(blend["blended_prob"] - 0.64) <= AI_WEIGHT_MAX + 1e-9
    assert blend["ai_weight"] <= AI_WEIGHT_MAX
    # AI-disabled mode: forecast passes through intact.
    disabled = client.post(
        "/api/ai/forecast_opinion",
        json={"symbol": "AAPL", "horizon": 5, "quant_prob": 0.64, "ai_enabled": False},
    )
    assert disabled.json()["blend"]["blended_prob"] == pytest.approx(0.64)
    assert disabled.json()["blend"]["ai_applied"] is False
    # Invalid horizon rejected (only 5/21/63).
    bad = client.post("/api/ai/forecast_opinion", json={"symbol": "AAPL", "horizon": 30})
    assert bad.status_code == 422
    bad_profile = client.post("/api/ai/insight", json={"symbol": "AAPL", "profile": "nope"})
    assert bad_profile.status_code == 422


def test_api_performance_and_health_redacted(empty_store):
    empty_store.put("openai", "api_key", "sk-live-redact-me")
    client = _client(empty_store)
    client.post("/api/ai/insight", json={"symbol": "AAPL", "profile": "quick_insight"})
    perf = client.get("/api/ai/providers/performance")
    assert perf.status_code == 200
    assert "sk-live-redact-me" not in perf.text
    health = client.post("/api/ai/providers/health/test", json={})
    assert health.status_code == 200
    assert "sk-live-redact-me" not in health.text
    names = {row["provider"] for row in health.json()["providers"]}
    assert {"gemini", "openai", "anthropic", "xai"} <= names
    single = client.post("/api/ai/providers/health/test", json={"provider": "openai"})
    assert single.json()["providers"][0]["configured"] is True
