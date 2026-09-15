"""AIRouter: profile map, evidence-hash cache, token logging, performance tracker.

- Profiles Quick Insight / Deep Research / Forecast Assist / Report all
  default to Gemini Flash (spec M4); overrides are constructor-injected so a
  provider swap requires zero analytics/frontend changes.
- Responses are cached by evidence-hash (+ profile/provider/model/horizon)
  with per-profile TTLs (quick/forecast short, deep/report longer).
- Resilience: per-profile timeouts (8s quick … 25s deep) via
  asyncio.wait_for, exponential-backoff retries on transient failures only,
  per-provider circuit breakers (no-key/validation stubs never trip them),
  and request coalescing (concurrent identical evidence_hash shares one
  in-flight provider call). get_insights_parallel() batches safely.
- Every call appends a redacted token-usage entry (never any key material)
  with input/output estimates for future billing (see TokenLedger).
- Provider/model performance is tracked by exchange + horizon for the
  comparison dashboard (spec M5: track by exchange and horizon).

Future billing stubs (NOT enforced yet):
- get_insight() accepts user_tier / call_type / token_credits and logs them.
  Planned mapping (enforce later, not today):
    Free:     20/day, Quick Insight only, no Deep Research, no Grok.
    Silver:   1000 Quick + 400 Forecast Assist + 100 Deep/Report per month.
    Gold:     Silver quotas + Grok (xai) provider access.
    Platinum: unlimited + Deep Research priority.
- TokenLedger below is the in-memory stub for that ledger (swap for
  Postgres/Redis; interface stays). Prompt-caching hooks live in
  backend/ai/providers/base.py::prompt_cache_hint (Anthropic ephemeral /
  OpenAI auto-prefix) — attached later without changing request shapes.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections import OrderedDict
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

# ---------------------------------------------------------------------------
# Per-profile timeout / retry / token / cache config (single source of truth).
#
# timeout_s:         asyncio.wait_for guard per attempt (8s quick … 25s deep).
# max_retries:       extra attempts after the first on TRANSIENT failures
#                    only (timeout / connection / 429 / 5xx). 4xx, validation
#                    stubs and no-key stubs never retry.
# backoff_base_s:    sleep = base * 2**attempt between retries (deterministic,
#                    no jitter so tests stay fast/stable).
# max_prompt_tokens: input budget — prompts truncate packet JSON to this
#                    (Quick ~300-600, Forecast ~1000, Deep 4000 max).
# max_output_tokens: completion cap passed to providers (token-efficient).
# cache_ttl_s:       response-cache TTL per profile (quick/forecast short,
#                    deep/report longer — stable evidence reuses longer).
# ---------------------------------------------------------------------------
PROFILE_CONFIG: dict[str, dict[str, Any]] = {
    "quick_insight": {
        "timeout_s": 8.0, "max_retries": 1, "backoff_base_s": 0.25,
        "max_prompt_tokens": 600, "max_output_tokens": 400,
        "cache_ttl_s": 1800, "description": "Quick Insight: fast, cheap, ~300-600 tokens",
    },
    "forecast_assist": {
        "timeout_s": 12.0, "max_retries": 2, "backoff_base_s": 0.4,
        "max_prompt_tokens": 1000, "max_output_tokens": 600,
        "cache_ttl_s": 1800, "description": "Forecast Assist: blended opinion, ~1000 tokens",
    },
    "deep_research": {
        "timeout_s": 25.0, "max_retries": 2, "backoff_base_s": 0.5,
        "max_prompt_tokens": 4000, "max_output_tokens": 1200,
        "cache_ttl_s": 3600, "description": "Deep Research: thorough, up to 4000 tokens",
    },
    "report": {
        "timeout_s": 20.0, "max_retries": 2, "backoff_base_s": 0.5,
        "max_prompt_tokens": 2000, "max_output_tokens": 1000,
        "cache_ttl_s": 7200, "description": "Report: scheduled output, ~2000 tokens",
    },
}

# Tier plan reference (FUTURE enforcement only — logged today, never gated):
#   Free:     20 calls/day, Quick Insight only (no deep_research, no xai/Grok).
#   Silver:   1000 Quick + 400 Forecast Assist + 100 Deep/Report per month.
#   Gold:     Silver quotas + Grok (xai) provider access.
#   Platinum: unlimited + Deep Research priority lane.
VALID_USER_TIERS: tuple[str, ...] = ("free", "silver", "gold", "platinum")


def get_profile_config(profile: str) -> dict[str, Any]:
    """Return the resilience/token/cache config for a profile (copy)."""
    key = normalize_profile(profile)
    return dict(PROFILE_CONFIG[key])


def normalize_profile(profile: str) -> str:
    key = (profile or "").strip().lower().replace(" ", "_").replace("-", "_")
    if key not in DEFAULT_PROFILE_MAP:
        raise ValueError(f"unknown AI profile: {profile!r}; expected one of {sorted(DEFAULT_PROFILE_MAP)}")
    return key


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token) for budget logging."""
    return max(1, len(text or "") // 4)


def _is_retryable_stub(opinion: AIOpinion) -> bool:
    """True when a stub opinion reflects a TRANSIENT failure worth retrying.

    Retryable: live-call/network/timeout/overload/429/5xx phrasing (including
    the router's own "live call failed: TimeoutError" marker).
    Never retryable: no-key stubs, validation stubs, circuit-open stubs —
    retrying those is pure waste.
    """
    try:
        reasons = " ".join(getattr(opinion, "limitations", []) or []).lower()
    except Exception:
        return False
    if not getattr(opinion, "stub", False):
        return False
    if "no api key" in reasons or "failed validation" in reasons or "circuit-open" in reasons or "circuit open" in reasons:
        return False
    markers = (
        "live call unavailable", "live call failed", "timeout", "timed out",
        "temporarily", "overloaded", "try again", "connection", "network",
        "http 429", "http 500", "http 502", "http 503", "http 504",
    )
    return any(marker in reasons for marker in markers)


class AICircuitBreaker:
    """Per-provider breaker: closed -> open after N consecutive TRANSIENT
    failures; half-open probe after reset_timeout_s. No-key and validation
    stubs never count (they are not provider failures)."""

    CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"

    def __init__(self, failure_threshold: int = 5, reset_timeout_s: float = 60.0) -> None:
        self.failure_threshold = max(1, int(failure_threshold))
        self.reset_timeout_s = max(1.0, float(reset_timeout_s))
        self.state = self.CLOSED
        self.consecutive_failures = 0
        self.opened_at: float | None = None

    def allow_request(self) -> bool:
        if self.state == self.CLOSED:
            return True
        if self.state == self.OPEN:
            if self.opened_at is not None and (time.monotonic() - self.opened_at) >= self.reset_timeout_s:
                self.state = self.HALF_OPEN
                return True
            return False
        return True  # half-open: permit the probe

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.state = self.CLOSED
        self.opened_at = None

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        if self.state == self.HALF_OPEN or self.consecutive_failures >= self.failure_threshold:
            self.state = self.OPEN
            self.opened_at = time.monotonic()


class TokenLedger:
    """In-memory token-usage ledger stub for future billing (NOT enforced).

    Tiers (future): Free 20/day Quick-only / Silver 1000+400+100 /
    Gold +Grok / Platinum unlimited+Deep. record() appends a redacted entry;
    totals() aggregates per (provider, model, profile). Swap the backing
    store for Postgres/Redis later; the interface stays.
    """

    MAX_ENTRIES = 5000

    def __init__(self, max_entries: int = MAX_ENTRIES) -> None:
        self._entries: list[dict[str, Any]] = []
        self._max = max(1, int(max_entries))

    def record(
        self,
        *,
        provider: str,
        model: str,
        profile: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cached: bool = False,
        stub: bool = False,
        user_tier: str | None = None,
        call_type: str | None = None,
        token_credits: int | None = None,
    ) -> dict[str, Any]:
        entry = {
            "provider": str(provider or "unknown"),
            "model": str(model or "unknown"),
            "profile": str(profile or "unknown"),
            "prompt_tokens": max(0, int(prompt_tokens or 0)),
            "completion_tokens": max(0, int(completion_tokens or 0)),
            "total_tokens": max(0, int(prompt_tokens or 0)) + max(0, int(completion_tokens or 0)),
            "cached": bool(cached),
            "stub": bool(stub),
            # Tier stubs: logged for future quota checks, never gated today.
            "user_tier": (str(user_tier).strip().lower() if user_tier else None),
            "call_type": (str(call_type).strip()[:64] if call_type else None),
            "token_credits": token_credits if isinstance(token_credits, int) else None,
            "ts": time.time(),
        }
        self._entries.append(entry)
        if len(self._entries) > self._max:
            del self._entries[: len(self._entries) - self._max]
        return entry

    def totals(self) -> list[dict[str, Any]]:
        groups: dict[tuple[str, str, str], dict[str, Any]] = {}
        for entry in self._entries:
            key = (entry["provider"], entry["model"], entry["profile"])
            bucket = groups.setdefault(key, {
                "provider": entry["provider"], "model": entry["model"],
                "profile": entry["profile"], "calls": 0,
                "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
            })
            bucket["calls"] += 1
            bucket["prompt_tokens"] += entry["prompt_tokens"]
            bucket["completion_tokens"] += entry["completion_tokens"]
            bucket["total_tokens"] += entry["total_tokens"]
        return sorted(groups.values(), key=lambda r: (r["provider"], r["model"], r["profile"]))

    def reset(self) -> None:
        self._entries.clear()


class ProviderPerformanceTracker:
    """In-memory provider/model scoreboard grouped by exchange + horizon.

    Swap the backing store for Postgres/Redis later; the interface stays.
    `correct` is optional (set once outcomes are known); latency/stub/error
    stats are recorded on every call. Bounded: the newest ``MAX_RECORDS``
    calls are kept (oldest dropped) so long-lived routers cannot grow
    without bound; summaries over the retained window are unaffected for
    normal (small) volumes.
    """

    MAX_RECORDS = 5000

    def __init__(self, max_records: int = MAX_RECORDS) -> None:
        self._records: list[dict[str, Any]] = []
        self._max_records = max(1, int(max_records))

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
        try:
            horizon_int = int(horizon)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            horizon_int = 0
        try:
            latency = float(latency_ms or 0.0)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            latency = 0.0
        if not math.isfinite(latency) or latency < 0:
            latency = 0.0
        try:
            prov = str(provider or "unknown")
        except Exception:
            prov = "unknown"
        try:
            mod = str(model or "unknown")
        except Exception:
            mod = "unknown"
        try:
            exch = str(exchange or "unknown").upper() or "unknown"
        except Exception:
            exch = "unknown"
        self._records.append({
            "provider": prov, "model": mod,
            "exchange": exch,
            "horizon": horizon_int,
            "correct": correct, "latency_ms": latency,
            "stub": bool(stub), "error": bool(error),
        })
        if len(self._records) > self._max_records:
            del self._records[: len(self._records) - self._max_records]

    def summary(
        self, *, exchange: str | None = None, horizon: int | None = None
    ) -> list[dict[str, Any]]:
        groups: dict[tuple[str, str, str, int], dict[str, Any]] = {}
        try:
            exch_filter = str(exchange).upper() if exchange else None
        except Exception:
            exch_filter = None
        horizon_filter: int | None = None
        if horizon is not None:
            try:
                horizon_filter = int(horizon)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return []
        for record in self._records:
            if exch_filter and record["exchange"] != exch_filter:
                continue
            if horizon_filter is not None and record["horizon"] != horizon_filter:
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
        cache_ttl_s: int = 3600,
        profile_config: dict[str, dict[str, Any]] | None = None,
        cache_backend: Any | None = None,
        cache_namespace: str = "ai",
    ) -> None:
        if providers is None:
            from backend.ai.providers import build_default_providers

            providers = build_default_providers(secret_store=secret_store)
        self.providers = dict(providers)
        self.profile_map: dict[str, tuple[str, str]] = dict(DEFAULT_PROFILE_MAP)
        for profile, target in (profile_map or {}).items():
            self.profile_map[normalize_profile(profile)] = (target[0], target[1])
        self.cache_size = max(1, int(cache_size))
        try:
            self.cache_ttl_s = max(60, int(cache_ttl_s))
        except (TypeError, ValueError):
            self.cache_ttl_s = 3600
        # Per-profile overrides (constructor-injected for tests); falls back
        # to PROFILE_CONFIG then to the global cache_ttl_s.
        self.profile_config: dict[str, dict[str, Any]] = {
            key: dict(cfg) for key, cfg in PROFILE_CONFIG.items()
        }
        for profile, cfg in (profile_config or {}).items():
            try:
                self.profile_config[normalize_profile(profile)].update(dict(cfg))
            except ValueError:
                continue
        self._cache: OrderedDict[str, tuple[float, AIOpinion]] = OrderedDict()
        self.token_log: list[dict[str, Any]] = []
        self.performance = ProviderPerformanceTracker()
        self.ledger = TokenLedger()
        self._breakers: dict[str, AICircuitBreaker] = {}
        self._inflight: dict[str, asyncio.Task] = {}
        self._inflight_lock = asyncio.Lock()
        # Distributed cache (opt-in): shared Redis via backend.cache when
        # configured, else the local OrderedDict above stays the only store.
        # Never raises; all dist ops are best-effort with local fallback.
        self._dist = cache_backend
        self._dist_ns = (cache_namespace or "ai").strip() or "ai"

    # -- routing ---------------------------------------------------------
    def resolve(self, profile: str) -> tuple[str, str]:
        key = normalize_profile(profile)
        provider_name, model = self.profile_map[key]
        if provider_name not in self.providers:
            raise ValueError(f"no provider registered for {provider_name!r}")
        return provider_name, model

    def profile_limits(self, profile: str) -> dict[str, Any]:
        """Effective resilience/token/cache config for a profile (copy)."""
        key = normalize_profile(profile)
        cfg = dict(PROFILE_CONFIG[key])
        cfg.update(self.profile_config.get(key, {}))
        return cfg

    def cache_ttl_for(self, profile: str) -> int:
        """Per-profile cache TTL (s); falls back to the router default."""
        try:
            return max(60, int(self.profile_limits(profile).get("cache_ttl_s", self.cache_ttl_s)))
        except (TypeError, ValueError):
            return self.cache_ttl_s

    def breaker_for(self, provider_name: str) -> AICircuitBreaker:
        breaker = self._breakers.get(provider_name)
        if breaker is None:
            breaker = AICircuitBreaker()
            self._breakers[provider_name] = breaker
        return breaker

    def breaker_state(self, provider_name: str) -> str:
        return self.breaker_for(provider_name).state

    def reset_breakers(self) -> None:
        self._breakers.clear()

    def cache_key(
        self, profile: str, packet: EvidencePacket, horizon: int | None,
        provider: str, model: str,
    ) -> str:
        horizon_part = str(horizon) if horizon in (5, 21, 63) else "-"
        return f"{profile}:{provider}:{model}:{packet.evidence_hash}:{horizon_part}"

    def clear_cache(self) -> None:
        # Evict this router's known keys from BOTH layers: get_insight reads
        # through local -> dist, so clearing local alone would leave live dist
        # copies behind that keep serving stale opinions (and keep tests that
        # expect a live attempt after clear from ever reaching the provider).
        # Best-effort; never raises.
        keys = list(self._cache.keys())
        self._cache.clear()
        for key in keys:
            self._dist_delete(key)

    def _cache_get(self, key: str) -> AIOpinion | None:
        """TTL-aware fetch: expired entries are dropped, never served."""
        hit = self._cache.get(key)
        if hit is None:
            return self._dist_get(key)
        try:
            expires_at, opinion = hit
        except (TypeError, ValueError):
            self._cache.pop(key, None)
            return self._dist_get(key)
        if expires_at < time.monotonic():
            self._cache.pop(key, None)
            # Expiry wins everywhere: drop the dist copy too so an expired
            # local entry can never be resurrected from the shared layer.
            self._dist_delete(key)
            return None
        self._cache.move_to_end(key)
        return opinion

    def _cache_put(self, key: str, opinion: AIOpinion, profile: str | None = None) -> None:
        ttl = self.cache_ttl_for(profile) if profile else self.cache_ttl_s
        self._cache[key] = (time.monotonic() + ttl, opinion)
        while len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)
        self._dist_put(key, opinion, ttl)

    def _dist_client(self) -> Any | None:
        if self._dist is not None:
            return self._dist
        try:
            from backend.cache import get_cache as _get_cache

            return _get_cache()
        except Exception:
            return None

    def _dist_key(self, key: str) -> str:
        return f"{self._dist_ns}:{key}"

    def _dist_get(self, key: str) -> AIOpinion | None:
        try:
            client = self._dist_client()
            if client is None:
                return None
            raw = client.get(self._dist_key(key))
            if not isinstance(raw, dict):
                return None
            return AIOpinion.model_validate(raw)
        except Exception:
            return None

    def _dist_put(self, key: str, opinion: AIOpinion, ttl: int) -> None:
        try:
            client = self._dist_client()
            if client is None:
                return
            client.set(self._dist_key(key), opinion.model_dump(mode="json"), ttl_s=int(ttl))
        except Exception:
            pass

    def _dist_delete(self, key: str) -> None:
        """Best-effort eviction of one dist entry (clear/expiry paths)."""
        try:
            client = self._dist_client()
            if client is None:
                return
            delete = getattr(client, "delete", None)
            if callable(delete):
                delete(self._dist_key(key))
        except Exception:
            pass

    # -- main entry -------------------------------------------------------
    async def get_insight(
        self,
        packet: EvidencePacket,
        *,
        profile: str = "quick_insight",
        horizon: int | None = None,
        timeout_s: float | None = None,
        # --- future tier-routing stubs (logged, NEVER enforced today) ---
        user_tier: str | None = None,
        call_type: str | None = None,
        token_credits: int | None = None,
    ) -> tuple[AIOpinion, bool]:
        """Return (opinion, cached). Malformed provider output degrades to a
        marked stub via the provider layer; this method never raises for
        provider failures (unknown profiles still raise ValueError).

        Resilience: cache -> open-breaker fast path -> in-flight coalescing
        (concurrent identical evidence_hash shares one provider call) ->
        timeout-guarded attempts with exponential backoff on transient
        failures only.
        """
        key = normalize_profile(profile)
        provider_name, model = self.resolve(key)
        provider = self.providers[provider_name]
        if model and getattr(provider, "model", None) != model:
            # Respect the profile map without mutating shared instances.
            provider = _with_model(provider, model)

        cache_hit_key = self.cache_key(key, packet, horizon, provider_name, provider.model)
        cached = self._cache_get(cache_hit_key)
        if cached is not None:
            self._log_tokens(
                key, provider_name, provider.model, packet, cached, True, 0.0,
                user_tier=user_tier, call_type=call_type, token_credits=token_credits,
            )
            return cached, True

        # -- request coalescing: dedup identical evidence_hash in-flight ----
        async with self._inflight_lock:
            inflight = self._inflight.get(cache_hit_key)
            if inflight is None or inflight.done():
                if inflight is not None:
                    self._inflight.pop(cache_hit_key, None)
                limits = self.profile_limits(key)
                task = asyncio.ensure_future(
                    self._fetch_with_resilience(
                        packet, profile=key, horizon=horizon,
                        provider_name=provider_name, provider=provider,
                        limits=limits, timeout_override=timeout_s,
                        user_tier=user_tier, call_type=call_type,
                        token_credits=token_credits,
                    )
                )
                self._inflight[cache_hit_key] = task
                leader = True
            else:
                task = inflight
                leader = False
        try:
            if leader:
                opinion, latency_ms, coalesced = await task
            else:
                opinion, latency_ms, _ = await asyncio.shield(task)
                coalesced = True
        finally:
            if leader:
                async with self._inflight_lock:
                    if self._inflight.get(cache_hit_key) is task:
                        self._inflight.pop(cache_hit_key, None)
        if not leader:
            self._log_tokens(
                key, provider_name, provider.model, packet, opinion, False,
                latency_ms, coalesced=True,
                user_tier=user_tier, call_type=call_type, token_credits=token_credits,
            )
        return opinion, False

    async def get_insights_parallel(
        self,
        packets: list[EvidencePacket],
        *,
        profile: str = "quick_insight",
        horizon: int | None = None,
        max_concurrency: int = 8,
        user_tier: str | None = None,
        call_type: str | None = None,
        token_credits: int | None = None,
    ) -> list[tuple[AIOpinion, bool]]:
        """Batch helper: concurrent get_insight calls behind a semaphore.

        Safe for bulk views — cache + coalescing dedup identical packets so
        N identical requests cost ~1 provider call.
        """
        key = normalize_profile(profile)
        semaphore = asyncio.Semaphore(max(1, int(max_concurrency)))

        async def _one(packet: EvidencePacket) -> tuple[AIOpinion, bool]:
            async with semaphore:
                return await self.get_insight(
                    packet, profile=key, horizon=horizon,
                    user_tier=user_tier, call_type=call_type,
                    token_credits=token_credits,
                )

        return list(await asyncio.gather(*(_one(packet) for packet in packets)))

    async def _fetch_with_resilience(
        self,
        packet: EvidencePacket,
        *,
        profile: str,
        horizon: int | None,
        provider_name: str,
        provider: BaseProvider,
        limits: dict[str, Any],
        timeout_override: float | None = None,
        user_tier: str | None = None,
        call_type: str | None = None,
        token_credits: int | None = None,
    ) -> tuple[AIOpinion, float, bool]:
        """Single-flight provider call with timeout/retry/breaker. Returns
        (opinion, latency_ms, coalesced=False). Never raises for provider
        failures (ValueError/TypeError contract errors still propagate)."""
        breaker = self.breaker_for(provider_name)
        cache_hit_key = self.cache_key(profile, packet, horizon, provider_name, provider.model)
        try:
            effective_timeout = float(timeout_override if timeout_override else limits.get("timeout_s", 8.0))
        except (TypeError, ValueError):
            effective_timeout = 8.0
        effective_timeout = min(max(effective_timeout, 1.0), 60.0)
        try:
            max_retries = max(0, int(limits.get("max_retries", 1)))
        except (TypeError, ValueError):
            max_retries = 1
        try:
            backoff_base = max(0.0, float(limits.get("backoff_base_s", 0.4)))
        except (TypeError, ValueError):
            backoff_base = 0.4

        if not breaker.allow_request():
            from backend.ai.providers.base import build_stub_opinion

            opinion = build_stub_opinion(
                packet, provider=provider_name, model=provider.model,
                horizon=horizon, reason="circuit-open: provider cooling down",
            )
            exchange = (packet.metadata.exchange_mic or "unknown").upper() or "unknown"
            try:
                horizon_tag = int(opinion.time_horizon_days)
            except (TypeError, ValueError):
                horizon_tag = horizon if horizon in (5, 21, 63) else 21
            self.performance.record(
                provider_name, provider.model, exchange, horizon_tag,
                latency_ms=0.0, stub=True, error="circuit_open",
            )
            self._log_tokens(
                profile, provider_name, provider.model, packet, opinion,
                False, 0.0, breaker_state=breaker.state,
                user_tier=user_tier, call_type=call_type, token_credits=token_credits,
            )
            self._cache_put(cache_hit_key, opinion, profile)
            return opinion, 0.0, False

        started = time.monotonic()
        opinion: AIOpinion | None = None
        error: str | None = None
        real_usage: dict[str, int] | None = None
        attempts = max_retries + 1
        for attempt in range(attempts):
            try:
                # Prefer real usage counts when the vendor exposes them;
                # fall back to plain insight() for test doubles without it.
                try:
                    _call = provider.insight_with_usage(packet, profile=profile, horizon=horizon)  # type: ignore[attr-defined]
                except AttributeError:
                    _call = provider.insight(packet, profile=profile, horizon=horizon)  # type: ignore[assignment]
                    _is_usage_tuple = False
                else:
                    _is_usage_tuple = True
                if _is_usage_tuple:
                    candidate, usage = await asyncio.wait_for(_call, timeout=effective_timeout)
                    if not isinstance(candidate, AIOpinion):
                        raise TypeError("provider must return AIOpinion")
                    if isinstance(usage, dict):
                        real_usage = usage
                else:
                    candidate = await asyncio.wait_for(_call, timeout=effective_timeout)
                    if not isinstance(candidate, AIOpinion):
                        raise TypeError("provider must return AIOpinion")
            except (ValueError, TypeError):
                # Unknown profile / contract violation: surface (422 path).
                raise
            except (asyncio.TimeoutError, TimeoutError) as exc:
                error = "TimeoutError"
                if attempt < attempts - 1:
                    await asyncio.sleep(backoff_base * (2**attempt))
                    continue
                from backend.ai.providers.base import build_stub_opinion

                opinion = build_stub_opinion(
                    packet, provider=provider_name, model=provider.model,
                    horizon=horizon,
                    reason=f"live call failed: TimeoutError after {attempts} attempt(s)",
                )
                break
            except Exception as exc:  # last-resort guard; providers already stub
                error = type(exc).__name__
                if attempt < attempts - 1:
                    await asyncio.sleep(backoff_base * (2**attempt))
                    continue
                from backend.ai.providers.base import build_stub_opinion

                opinion = build_stub_opinion(
                    packet, provider=provider_name, model=provider.model,
                    horizon=horizon, reason=f"live call failed: {type(exc).__name__}",
                )
                break
            else:
                if candidate.stub and _is_retryable_stub(candidate) and attempt < attempts - 1:
                    error = "transient_stub"
                    await asyncio.sleep(backoff_base * (2**attempt))
                    continue
                opinion = candidate
                if candidate.stub and _is_retryable_stub(candidate):
                    error = "transient_stub"
                elif candidate.stub:
                    error = None  # non-transient stub (no-key/validation): not an error
                else:
                    error = None
                break

        assert opinion is not None
        latency_ms = (time.monotonic() - started) * 1000.0

        # Breaker bookkeeping: only TRANSIENT failures trip it.
        if error in ("TimeoutError", "transient_stub") or (
            error is not None and opinion.stub and _is_retryable_stub(opinion)
        ):
            breaker.record_failure()
        elif error is not None and not opinion.stub:
            breaker.record_failure()  # pragma: no cover — providers stub instead
        elif error is not None and opinion.stub:
            # Non-transient stub surfaced via exception path: still a failure
            # signal only if it looks transient; default to success so no-key
            # stubs never open the circuit.
            pass
        else:
            breaker.record_success()

        exchange = (packet.metadata.exchange_mic or "unknown").upper() or "unknown"
        try:
            horizon_tag = int(opinion.time_horizon_days)
        except (TypeError, ValueError):
            horizon_tag = horizon if horizon in (5, 21, 63) else 21
        self.performance.record(
            provider_name, provider.model, exchange, horizon_tag,
            latency_ms=latency_ms, stub=bool(opinion.stub), error=error,
        )
        self._log_tokens(
            profile, provider_name, provider.model, packet, opinion,
            False, latency_ms, timeout_s=effective_timeout,
            retries=max_retries, breaker_state=breaker.state,
            user_tier=user_tier, call_type=call_type, token_credits=token_credits,
            usage=real_usage,
        )

        self._cache_put(cache_hit_key, opinion, profile)
        return opinion, latency_ms, False

    def _log_tokens(
        self, profile: str, provider: str, model: str,
        packet: EvidencePacket, opinion: AIOpinion, cached: bool, latency_ms: float,
        *,
        coalesced: bool = False,
        timeout_s: float | None = None,
        retries: int | None = None,
        breaker_state: str | None = None,
        user_tier: str | None = None,
        call_type: str | None = None,
        token_credits: int | None = None,
        usage: dict[str, int] | None = None,
    ) -> None:
        # Redacted by construction: packet/opinion never carry secrets.
        # Real provider usage wins when finite; estimate is the fallback.
        try:
            real_prompt = None
            real_comp = None
            if isinstance(usage, dict):
                try:
                    from backend.ai.providers.base import BaseProvider as _BP

                    real_prompt = _BP._finite_usage(usage.get("prompt_tokens"))
                    real_comp = _BP._finite_usage(usage.get("completion_tokens"))
                except Exception:
                    real_prompt, real_comp = None, None
            if real_prompt is not None or real_comp is not None:
                prompt_tokens = real_prompt or 0
                completion_tokens = real_comp or 0
                usage_source = "provider"
            else:
                raise ValueError("no real usage")
        except Exception:
            try:
                from backend.ai.prompts import render_prompt

                prompt_text = render_prompt(profile, packet)
                prompt_tokens = estimate_tokens(prompt_text)
            except Exception:
                prompt_chars = len(packet.evidence_hash) + len(packet.symbol) + 6000
                prompt_tokens = max(1, prompt_chars // 4)  # legacy fallback
            try:
                completion_tokens = estimate_tokens(opinion.model_dump_json())
            except Exception:
                completion_tokens = 100
            usage_source = "estimate"
        entry: dict[str, Any] = {
            "profile": profile, "provider": provider, "model": model,
            "packet_id": packet.packet_id, "evidence_hash": packet.evidence_hash,
            # Legacy key kept for backward-compat dashboards/tests.
            "prompt_tokens_est": prompt_tokens,
            # Billing-grade split (input/output) + total.
            "prompt_tokens": prompt_tokens,
            "completion_tokens_est": completion_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens_est": prompt_tokens + completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "usage_source": usage_source,
            "cached": cached, "coalesced": coalesced,
            "stub": bool(opinion.stub),
            "latency_ms": round(latency_ms, 2),
        }
        if timeout_s is not None:
            entry["timeout_s"] = timeout_s
        if retries is not None:
            entry["retries"] = retries
        if breaker_state is not None:
            entry["breaker"] = breaker_state
        # Tier stubs: logged for future quota/billing work, never gated.
        if user_tier is not None:
            entry["user_tier"] = str(user_tier).strip().lower()[:32]
        if call_type is not None:
            entry["call_type"] = str(call_type).strip()[:64]
        if token_credits is not None:
            entry["token_credits"] = token_credits
        self.token_log.append(entry)
        if len(self.token_log) > 2000:
            del self.token_log[: len(self.token_log) - 2000]
        try:
            self.ledger.record(
                provider=provider, model=model, profile=profile,
                prompt_tokens=0 if cached or coalesced else prompt_tokens,
                completion_tokens=0 if cached or coalesced else completion_tokens,
                cached=cached or coalesced, stub=bool(opinion.stub),
                user_tier=user_tier, call_type=call_type,
                token_credits=token_credits,
            )
        except Exception:
            pass


def _with_model(provider: BaseProvider, model: str) -> BaseProvider:
    """Shallow copy of a provider bound to a different model name."""
    clone = object.__new__(type(provider))
    clone.__dict__.update(provider.__dict__)
    clone.model = model
    return clone
