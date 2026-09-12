"""AIRouter: profile map, evidence-hash cache, token logging, performance tracker.

- Profiles Quick Insight / Deep Research / Forecast Assist / Report all
  default to Gemini Flash (spec M4); overrides are constructor-injected so a
  provider swap requires zero analytics/frontend changes.
- Responses are cached by evidence-hash (+ profile/provider/model/horizon).
- Every call appends a redacted token-usage entry (never any key material).
- Provider/model performance is tracked by exchange + horizon for the
  comparison dashboard (spec M5: track by exchange and horizon).
"""

from __future__ import annotations

import time
from collections import OrderedDict, defaultdict
from typing import Any

from backend.ai.prompts import PROFILES
from backend.ai.providers.base import BaseProvider
from backend.ai.schemas import AIOpinion, EvidencePacket

DEFAULT_MODEL_BY_PROVIDER = {
    "gemini": "gemini-3.7-flash",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-3-5-haiku-latest",
    "xai": "grok-3-mini",
}

DEFAULT_PROFILE_MAP: dict[str, tuple[str, str]] = {
    profile: ("gemini", DEFAULT_MODEL_BY_PROVIDER["gemini"]) for profile in PROFILES
}


def normalize_profile(profile: str) -> str:
    key = (profile or "").strip().lower().replace(" ", "_").replace("-", "_")
    if key not in DEFAULT_PROFILE_MAP:
        raise ValueError(f"unknown AI profile: {profile!r}; expected one of {sorted(DEFAULT_PROFILE_MAP)}")
    return key


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token) for budget logging."""
    return max(1, len(text or "") // 4)


class ProviderPerformanceTracker:
    """In-memory provider/model scoreboard grouped by exchange + horizon.

    Swap the backing store for Postgres/Redis later; the interface stays.
    `correct` is optional (set once outcomes are known); latency/stub/error
    stats are recorded on every call.
    """

    def __init__(self) -> None:
        self._records: list[dict[str, Any]] = []

    def record(
        self,
        provider: str,
        model: str,
        exchange: str,
        horizon: int,
        *,
        correct: bool | None = None,
        latency_ms: float = 0.0,
        stub: bool = False,
        error: str | None = None,
    ) -> None:
        self._records.append({
            "provider": provider, "model": model,
            "exchange": (exchange or "unknown").upper() or "unknown",
            "horizon": int(horizon),
            "correct": correct, "latency_ms": float(latency_ms or 0.0),
            "stub": bool(stub), "error": bool(error),
        })

    def summary(
        self, *, exchange: str | None = None, horizon: int | None = None
    ) -> list[dict[str, Any]]:
        groups: dict[tuple[str, str, str, int], dict[str, Any]] = {}
        for record in self._records:
            if exchange and record["exchange"] != exchange.upper():
                continue
            if horizon is not None and record["horizon"] != int(horizon):
                continue
            key = (record["provider"], record["model"], record["exchange"], record["horizon"])
            bucket = groups.setdefault(key, {
                "provider": record["provider"], "model": record["model"],
                "exchange": record["exchange"], "horizon": record["horizon"],
                "calls": 0, "stub_calls": 0, "errors": 0,
                "decided": 0, "correct": 0, "latency_ms_total": 0.0,
            })
            bucket["calls"] += 1
            bucket["stub_calls"] += 1 if record["stub"] else 0
            bucket["errors"] += 1 if record["error"] else 0
            bucket["latency_ms_total"] += record["latency_ms"]
            if record["correct"] is not None:
                bucket["decided"] += 1
                bucket["correct"] += 1 if record["correct"] else 0
        rows: list[dict[str, Any]] = []
        for bucket in groups.values():
            calls = bucket.pop("latency_ms_total")
            decided = bucket.pop("decided")
            correct = bucket.pop("correct")
            rows.append({
                **bucket,
                "accuracy": round(correct / decided, 4) if decided else None,
                "decided": decided,
                "stub_rate": round(bucket["stub_calls"] / bucket["calls"], 4) if bucket["calls"] else 0.0,
                "error_rate": round(bucket["errors"] / bucket["calls"], 4) if bucket["calls"] else 0.0,
                "avg_latency_ms": round(calls / bucket["calls"], 2) if bucket["calls"] else 0.0,
            })
        rows.sort(key=lambda row: (row["provider"], row["model"], row["exchange"], row["horizon"]))
        return rows

    def reset(self) -> None:
        self._records.clear()


class AIRouter:
    """Routes evidence packets to providers with caching + observability."""

    def __init__(
        self,
        *,
        providers: dict[str, BaseProvider] | None = None,
        profile_map: dict[str, tuple[str, str]] | None = None,
        secret_store: Any | None = None,
        cache_size: int = 256,
    ) -> None:
        if providers is None:
            from backend.ai.providers import build_default_providers

            providers = build_default_providers(secret_store=secret_store)
        self.providers = dict(providers)
        self.profile_map: dict[str, tuple[str, str]] = dict(DEFAULT_PROFILE_MAP)
        for profile, target in (profile_map or {}).items():
            self.profile_map[normalize_profile(profile)] = (target[0], target[1])
        self.cache_size = max(1, int(cache_size))
        self._cache: OrderedDict[str, AIOpinion] = OrderedDict()
        self.token_log: list[dict[str, Any]] = []
        self.performance = ProviderPerformanceTracker()

    # -- routing ---------------------------------------------------------
    def resolve(self, profile: str) -> tuple[str, str]:
        key = normalize_profile(profile)
        provider_name, model = self.profile_map[key]
        if provider_name not in self.providers:
            raise ValueError(f"no provider registered for {provider_name!r}")
        return provider_name, model

    def cache_key(
        self, profile: str, packet: EvidencePacket, horizon: int | None,
        provider: str, model: str,
    ) -> str:
        horizon_part = str(horizon) if horizon in (5, 21, 63) else "-"
        return f"{profile}:{provider}:{model}:{packet.evidence_hash}:{horizon_part}"

    def clear_cache(self) -> None:
        self._cache.clear()

    # -- main entry -------------------------------------------------------
    async def get_insight(
        self,
        packet: EvidencePacket,
        *,
        profile: str = "quick_insight",
        horizon: int | None = None,
    ) -> tuple[AIOpinion, bool]:
        """Return (opinion, cached). Malformed provider output degrades to a
        marked stub via the provider layer; this method never raises for
        provider failures (unknown profiles still raise ValueError)."""
        key = normalize_profile(profile)
        provider_name, model = self.resolve(key)
        provider = self.providers[provider_name]
        if model and getattr(provider, "model", None) != model:
            # Respect the profile map without mutating shared instances.
            provider = _with_model(provider, model)

        cache_hit_key = self.cache_key(key, packet, horizon, provider_name, provider.model)
        cached = self._cache.get(cache_hit_key)
        if cached is not None:
            self._cache.move_to_end(cache_hit_key)
            self._log_tokens(key, provider_name, provider.model, packet, cached, True, 0.0)
            return cached, True

        started = time.monotonic()
        try:
            opinion = await provider.insight(packet, profile=key, horizon=horizon)
            error: str | None = None
            if not isinstance(opinion, AIOpinion):
                raise TypeError("provider must return AIOpinion")
        except (ValueError, TypeError) as exc:
            # Unknown profile / contract violation: surface to caller (422).
            raise
        except Exception as exc:  # last-resort guard; providers already stub
            from backend.ai.providers.base import build_stub_opinion

            opinion = build_stub_opinion(
                packet, provider=provider_name, model=provider.model,
                horizon=horizon, reason=f"router fallback: {type(exc).__name__}",
            )
            error = type(exc).__name__
        latency_ms = (time.monotonic() - started) * 1000.0

        exchange = (packet.metadata.exchange_mic or "unknown").upper() or "unknown"
        self.performance.record(
            provider_name, provider.model, exchange, int(opinion.time_horizon_days),
            latency_ms=latency_ms, stub=bool(opinion.stub), error=error,
        )
        self._log_tokens(key, provider_name, provider.model, packet, opinion, False, latency_ms)

        self._cache[cache_hit_key] = opinion
        while len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)
        return opinion, False

    def _log_tokens(
        self, profile: str, provider: str, model: str,
        packet: EvidencePacket, opinion: AIOpinion, cached: bool, latency_ms: float,
    ) -> None:
        # Redacted by construction: packet/opinion never carry secrets.
        prompt_chars = len(packet.evidence_hash) + len(packet.symbol) + 6000
        self.token_log.append({
            "profile": profile, "provider": provider, "model": model,
            "packet_id": packet.packet_id, "evidence_hash": packet.evidence_hash,
            "prompt_tokens_est": estimate_tokens("x" * prompt_chars),
            "cached": cached, "stub": bool(opinion.stub),
            "latency_ms": round(latency_ms, 2),
        })
        if len(self.token_log) > 2000:
            del self.token_log[: len(self.token_log) - 2000]


def _with_model(provider: BaseProvider, model: str) -> BaseProvider:
    """Shallow copy of a provider bound to a different model name."""
    clone = object.__new__(type(provider))
    clone.__dict__.update(provider.__dict__)
    clone.model = model
    return clone
