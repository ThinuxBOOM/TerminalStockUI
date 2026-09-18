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


def _clear_global_ai_cache() -> None:
    """Evict the process-global dist cache (backend.cache singleton).

    AIRouter reads through local -> global dist, and every test below uses
    the identical evidence packet (same dist key). Without this, whichever
    test runs first poisons the rest with cache hits (0 provider calls,
    zeroed token logs, breakers that never trip). Cache is perf-only, so
    clearing between tests is always safe. Never raises.
    """
    try:
        from backend.cache import get_cache as _get_cache

        _c = _get_cache()
        _clear = getattr(_c, "clear", None)
        if callable(_clear):
            _clear()
    except Exception:
        pass


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
    _clear_global_ai_cache()
    yield store
    base_module.set_default_secret_store(None)
    secrets_module.reset_fernet()
    ai_api.reset_ai_router()
    _clear_global_ai_cache()


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
    from backend.tests.auth_helpers import inject_admin_auth

    app = FastAPI()
    app.include_router(ai_api.router)
    inject_admin_auth(app)

    async def _no_market_dependency():
        return None  # endpoints resolve market data internally; offline stub covers it

    _ = _no_market_dependency
    return TestClient(app)


def test_api_insight_roundtrip(empty_store):
    """Fail-closed: no key -> 423 (never a stub 200); wire never serves stubs."""
    client = _client(empty_store)
    response = client.post("/api/ai/insight", json={"symbol": "AAPL", "profile": "quick_insight"})
    assert response.status_code == 423, response.text
    assert "AI disabled" in response.text
    assert "api_key" not in response.text.lower()
    # Wire guard: stub opinions never reach the response (unit).
    from backend.ai.providers.base import build_stub_opinion

    from backend.api.ai import _refuse_stub_opinion
    import pytest as _pt

    stub = build_stub_opinion(_packet(), provider="gemini", model="gemini-3.7-flash")
    assert stub.stub is True
    with _pt.raises(Exception) as exc:
        _refuse_stub_opinion(stub, context="insight")
    assert getattr(exc.value, "status_code", None) == 502


def test_api_forecast_opinion_with_blend_preview(empty_store, monkeypatch):
    """Fail-closed: ai_enabled True with no key -> 423; disabled -> deterministic passthrough."""
    from backend.api import ai as _ai_api

    client = _client(empty_store)
    response = client.post(
        "/api/ai/forecast_opinion",
        json={"symbol": "AAPL", "horizon": 21, "quant_prob": 0.64,
              "ai_weight": 0.2, "ai_enabled": True},
    )
    assert response.status_code == 423, response.text
    # AI-disabled mode: deterministic passthrough without a live AI call.
    # Mock evidence packet (no network) — blend logic is what is under test.
    monkeypatch.setattr(_ai_api, "_build_packet", lambda symbol: _packet(symbol))
    disabled = client.post(
        "/api/ai/forecast_opinion",
        json={"symbol": "AAPL", "horizon": 7, "quant_prob": 0.64, "ai_enabled": False},
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["blend"]["blended_prob"] == pytest.approx(0.64)
    assert disabled.json()["blend"]["ai_applied"] is False
    # Invalid horizon rejected (only 1/7/14/21).
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


# ---------------------------------------------------------------------------
# Efficiency + resilience (Agent 4): per-profile timeout/token/cache config,
# input/output token logging, request coalescing, timeout stubs, circuit
# breaker, parallel batch, tier stubs (logged, never enforced).
# ---------------------------------------------------------------------------


def test_profile_config_timeouts_tokens_cache():
    from backend.ai.router import PROFILE_CONFIG

    assert set(PROFILE_CONFIG) == {"quick_insight", "forecast_assist", "deep_research", "report"}
    quick, forecast, deep, report = (
        PROFILE_CONFIG["quick_insight"], PROFILE_CONFIG["forecast_assist"],
        PROFILE_CONFIG["deep_research"], PROFILE_CONFIG["report"],
    )
    assert quick["timeout_s"] == 60.0
    assert deep["timeout_s"] == 60.0
    assert quick["max_prompt_tokens"] <= 600
    assert forecast["max_prompt_tokens"] == 1000
    assert deep["max_prompt_tokens"] == 4000
    assert report["cache_ttl_s"] >= deep["cache_ttl_s"] >= quick["timeout_s"]
    # DEFAULT_PROFILE_MAP untouched: every profile still defaults to Gemini Flash.
    for profile, (provider, model) in DEFAULT_PROFILE_MAP.items():
        assert provider == "gemini" and "flash" in model


def test_token_log_splits_input_output(empty_store):
    router = AIRouter(secret_store=empty_store)
    packet = _packet()
    asyncio.run(router.get_insight(packet, profile="quick_insight"))
    entry = router.token_log[-1]
    for field in ("prompt_tokens", "prompt_tokens_est", "completion_tokens_est",
                  "total_tokens_est", "cached", "stub", "latency_ms"):
        assert field in entry, field
    assert entry["total_tokens_est"] == entry["prompt_tokens"] + entry["completion_tokens_est"]
    assert entry["prompt_tokens"] > 0 and entry["completion_tokens_est"] > 0
    assert "api_key" not in str(entry)
    # Ledger mirrors the call for future billing.
    totals = router.ledger.totals()
    assert totals and totals[0]["total_tokens"] > 0


def test_request_coalescing_dedups_inflight(empty_store):
    import asyncio as _asyncio

    calls = {"n": 0}

    class SlowProvider(BaseProvider):
        name = "gemini"
        default_model = "gemini-3.7-flash"

        async def insight(self, packet, *, profile="quick_insight", horizon=None):
            calls["n"] += 1
            await _asyncio.sleep(0.2)
            return AIOpinion(
                direction="neutral", probability=0.5,
                time_horizon_days=horizon or 21,
                catalysts=["trend"], risks=["valuation"],
                evidence_ids=packet.evidence_ids or ["ev-1"],
                limitations=["Not investment advice."],
                provider=self.name, model=self.model,
            )

    router = AIRouter(providers={"gemini": SlowProvider(secret_store=empty_store)})

    async def _burst():
        packet = _packet()
        return await _asyncio.gather(*(
            router.get_insight(packet, profile="quick_insight") for _ in range(5)
        ))

    results = asyncio.run(_burst())
    assert calls["n"] == 1  # 5 concurrent identical packets -> 1 provider call
    assert all(isinstance(opinion, AIOpinion) for opinion, _ in results)  # all resolved
    assert {opinion.probability for opinion, _ in results} == {0.5}
    coalesced = [e for e in router.token_log if e.get("coalesced")]
    assert len(coalesced) == 4  # leader logs normally, 4 followers coalesced


def test_slow_provider_degrades_to_timeout_stub(empty_store):
    import asyncio as _asyncio

    class HangingProvider(BaseProvider):
        name = "gemini"
        default_model = "gemini-3.7-flash"

        async def insight(self, packet, *, profile="quick_insight", horizon=None):
            await _asyncio.sleep(30)  # far beyond any test timeout
            raise AssertionError("must never get here")

    router = AIRouter(
        providers={"gemini": HangingProvider(secret_store=empty_store)},
        profile_config={"quick_insight": {"max_retries": 0, "backoff_base_s": 0.0}},
    )
    packet = _packet()
    opinion, cached = asyncio.run(
        router.get_insight(packet, profile="quick_insight", timeout_s=1.0)
    )
    assert cached is False
    assert isinstance(opinion, AIOpinion) and opinion.stub is True
    assert "timeout" in " ".join(opinion.limitations).lower()
    assert router.breaker_state("gemini") == "closed"  # single timeout != open


def test_circuit_breaker_opens_on_repeated_transient_failures(empty_store):
    class BoomProvider(BaseProvider):
        name = "gemini"
        default_model = "gemini-3.7-flash"
        calls = 0

        async def insight(self, packet, *, profile="quick_insight", horizon=None):
            type(self).calls += 1
            raise RuntimeError("boom")

    provider = BoomProvider(secret_store=empty_store)
    router = AIRouter(
        providers={"gemini": provider},
        profile_config={"quick_insight": {"max_retries": 0, "backoff_base_s": 0.0}},
    )
    packet = _packet()
    for _ in range(5):  # failure threshold -> breaker opens
        opinion, _ = asyncio.run(router.get_insight(packet, profile="quick_insight"))
        assert opinion.stub is True
        router.clear_cache()  # force a live attempt each round
    assert router.breaker_state("gemini") == "open"
    before = BoomProvider.calls
    opinion, _ = asyncio.run(router.get_insight(packet, profile="quick_insight"))
    assert opinion.stub is True  # fast circuit-open stub, provider untouched
    assert BoomProvider.calls == before
    assert "circuit-open" in " ".join(opinion.limitations).lower()


def test_no_key_stubs_never_trip_breaker(empty_store):
    router = AIRouter(secret_store=empty_store)  # no keys -> stub path
    packet = _packet()
    for _ in range(7):
        opinion, _ = asyncio.run(router.get_insight(packet, profile="quick_insight"))
        assert opinion.stub is True
        router.clear_cache()
    assert router.breaker_state("gemini") == "closed"


def test_parallel_batch_dedups_identical_packets(empty_store):
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
    results = asyncio.run(router.get_insights_parallel([packet, packet, packet]))
    assert len(results) == 3
    assert calls["n"] == 1
    assert all(isinstance(opinion, AIOpinion) for opinion, _ in results)


def test_tier_stubs_logged_never_enforced(empty_store):
    router = AIRouter(secret_store=empty_store)
    packet = _packet()
    # Platinum + deep works exactly like free + quick: no gating today.
    deep_opinion, _ = asyncio.run(router.get_insight(
        packet, profile="deep_research", user_tier="platinum",
        call_type="deep_research", token_credits=999,
    ))
    quick_opinion, _ = asyncio.run(router.get_insight(
        packet, profile="quick_insight", user_tier="free", call_type="insight",
    ))
    assert isinstance(deep_opinion, AIOpinion) and isinstance(quick_opinion, AIOpinion)
    tiers = [e.get("user_tier") for e in router.token_log]
    assert "platinum" in tiers and "free" in tiers
    ledger_rows = router.ledger.totals()
    assert {row["profile"] for row in ledger_rows} >= {"deep_research", "quick_insight"}


def test_per_profile_cache_ttl(empty_store):
    router = AIRouter(secret_store=empty_store)
    assert router.cache_ttl_for("report") >= router.cache_ttl_for("deep_research")
    assert router.cache_ttl_for("deep_research") > router.cache_ttl_for("quick_insight")
    packet = _packet()
    asyncio.run(router.get_insight(packet, profile="report"))
    key = router.cache_key("report", packet, None, "gemini", "gemini-3.7-flash")
    import time as _time

    remaining = router._cache[key][0] - _time.monotonic()
    assert remaining > router.cache_ttl_for("quick_insight")  # report TTL is longer
