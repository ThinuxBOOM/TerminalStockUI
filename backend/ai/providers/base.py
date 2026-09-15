"""BaseProvider: shared interface, stub fallback, and secret hygiene.

Rules enforced here for every vendor:
- API keys live encrypted at rest (backend.security.secrets.EncryptedSecretStore)
  and are decrypted ONLY inside insight() at call time — never at import,
  never stored on the instance, never returned or logged.
- Low temperature + JSON-only prompts come from backend/ai/prompts/.
- No key configured (or any call/validation failure) -> return a clearly
  marked stub AIOpinion (stub=True). Providers NEVER raise for missing keys
  and NEVER crash the API.
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from backend.ai.schemas import AIOpinion, EvidencePacket, parse_opinion_strict

# Per-profile output-token caps (mirror router.PROFILE_CONFIG; duplicated
# here so providers work without importing the router at module load time —
# router is the source of truth for timeout/retry/cache, this is the local
# fallback for max_output_tokens only).
PROFILE_MAX_OUTPUT_TOKENS: dict[str, int] = {
    "quick_insight": 400,
    "forecast_assist": 600,
    "deep_research": 1200,
    "report": 1000,
}

PROFILE_TIMEOUT_FALLBACK: dict[str, float] = {
    "quick_insight": 8.0,
    "forecast_assist": 12.0,
    "deep_research": 25.0,
    "report": 20.0,
}


def max_output_tokens_for_profile(profile: str) -> int:
    """Max completion tokens for a profile (prompt-capped, token-efficient)."""
    key = (profile or "").strip().lower().replace(" ", "_").replace("-", "_")
    try:
        from backend.ai.router import PROFILE_CONFIG  # lazy: avoid import cycle

        return int(PROFILE_CONFIG.get(key, {}).get("max_output_tokens", PROFILE_MAX_OUTPUT_TOKENS.get(key, 400)))
    except Exception:
        return PROFILE_MAX_OUTPUT_TOKENS.get(key, 400)


def timeout_for_profile(profile: str) -> float:
    """HTTP timeout (s) for a profile: 8s quick … 25s deep."""
    key = (profile or "").strip().lower().replace(" ", "_").replace("-", "_")
    try:
        from backend.ai.router import PROFILE_CONFIG  # lazy: avoid import cycle

        return float(PROFILE_CONFIG.get(key, {}).get("timeout_s", PROFILE_TIMEOUT_FALLBACK.get(key, 8.0)))
    except Exception:
        return PROFILE_TIMEOUT_FALLBACK.get(key, 8.0)


def prompt_cache_hint(provider_name: str, profile: str) -> dict[str, Any]:
    """Future prompt-caching hooks (NOT enforced yet — stub for billing work).

    - Anthropic: return {"cache_control": {"type": "ephemeral"}} to attach to
      the system block / long evidence prefix once prompt-caching is enabled.
    - OpenAI: automatic prompt caching applies to prefixes >= 1024 tokens;
      keep the template prefix stable so repeated evidence packets hit it.
    Callers ignore the return value today; it documents where cache
    directives plug in without changing request shapes.
    """
    name = (provider_name or "").strip().lower()
    if name == "anthropic":
        return {"cache_control": {"type": "ephemeral"}, "profile": profile}
    if name == "openai":
        return {"prompt_cache": "auto-prefix", "profile": profile}
    return {"prompt_cache": "unsupported", "profile": profile}


def _is_transient_failure(status_code: int | None, exc: BaseException | None = None) -> bool:
    """Retryable = timeouts/connection errors, HTTP 429, 5xx. Never 4xx."""
    if exc is not None:
        try:
            import httpx

            if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError)):
                return True
        except Exception:
            pass
        name = type(exc).__name__.lower()
        if "timeout" in name or "connect" in name or "network" in name:
            return True
    if status_code is None:
        return False
    return status_code == 429 or 500 <= status_code <= 599

_default_store: Any | None = None


def get_default_secret_store() -> Any:
    """Process-wide encrypted secret store (swap backend for Vault/KMS later)."""
    global _default_store
    if _default_store is None:
        from backend.security.secrets import EncryptedSecretStore

        _default_store = EncryptedSecretStore()
    return _default_store


def set_default_secret_store(store: Any | None) -> None:
    """Test hook: inject an EncryptedSecretStore (or reset with None)."""
    global _default_store
    _default_store = store


def build_stub_opinion(
    packet: EvidencePacket,
    *,
    provider: str,
    model: str,
    horizon: int | None = None,
    reason: str = "no API key configured",
) -> AIOpinion:
    """Clearly marked offline opinion. Always validates; never crashes."""
    horizon_value = horizon if horizon in (5, 21, 63) else 21
    evidence_ids = list(packet.evidence_ids) or ["packet:empty-stub"]
    catalysts = [item.label for item in packet.top_bullish[:2]]
    risks = [item.label for item in packet.top_risks[:2]]
    limitations = [
        f"Stub opinion from provider '{provider}': {reason}. No live model call was made.",
        "Deterministic analytics remain the source of truth; AI weight should stay at 0 until a live opinion is available.",
    ]
    for extra in packet.limitations[:3]:
        if extra not in limitations:
            limitations.append(extra)
    return AIOpinion(
        direction="neutral",
        probability=0.5,
        time_horizon_days=horizon_value,
        catalysts=catalysts[:5],
        risks=risks[:5],
        evidence_ids=evidence_ids[:20],
        limitations=limitations[:10],
        provider=provider,
        model=model,
        stub=True,
    )


class BaseProvider(ABC):
    """Async insight interface shared by all vendors."""

    name: ClassVar[str] = "base"
    default_model: ClassVar[str] = "unknown"
    api_key_label: ClassVar[str] = "api_key"

    def __init__(self, *, model: str | None = None, secret_store: Any | None = None) -> None:
        self.model = (model or self.default_model).strip() or self.default_model
        self._injected_store = secret_store

    # -- secrets (call time only) --------------------------------------
    def _store(self) -> Any:
        return self._injected_store if self._injected_store is not None else get_default_secret_store()

    def _load_api_key(self) -> str | None:
        """Decrypt the provider key at call time. Returns None when absent.

        The plaintext key is never cached on self and never logged.
        Serverless fallback: when the encrypted store has no key (fresh
        Vercel invocation), read ``<PROVIDER>_API_KEY`` from the environment
        (e.g. ``GEMINI_API_KEY``). Vercel env vars are the persistent key
        path on serverless; the UI save flow needs a DB-backed endpoint
        before it can work there.
        DB fallback (Phase 3c, LAST resort): when neither the secret
        store nor the environment yields a key, consult the encrypted
        DB row via backend.security.store_db.get_db_secret (same
        SECRET_KEY-derived Fernet; ANY DB/decrypt issue -> None).
        """
        try:
            store = self._store()
            key = store.get(self.name, self.api_key_label)
        except KeyError:
            key = None
        except Exception:
            return None
        if isinstance(key, str) and key.strip():
            return key
        import os

        env_key = os.getenv(f"{self.name.upper()}_API_KEY", "")
        if isinstance(env_key, str) and env_key.strip():
            return env_key.strip()
        try:
            from backend.security.store_db import get_db_secret

            db_key = get_db_secret(self.name)
        except Exception:
            return None
        if isinstance(db_key, str) and db_key.strip():
            return db_key
        return None

    def is_configured(self) -> bool:
        try:
            if bool(self._store().has(self.name, self.api_key_label)):
                return True
        except Exception:
            pass
        import os

        if bool(os.getenv(f"{self.name.upper()}_API_KEY", "").strip()):
            return True
        try:
            from backend.security.store_db import db_configured

            if bool(db_configured(self.name)):
                return True
        except Exception:
            pass
        return False

    # -- interface ------------------------------------------------------
    @abstractmethod
    async def insight(
        self,
        packet: EvidencePacket,
        *,
        profile: str = "quick_insight",
        horizon: int | None = None,
    ) -> AIOpinion:
        """Return a validated AIOpinion (or marked stub). Must never raise
        for missing keys / network / validation failures."""

    async def insight_with_usage(
        self,
        packet: EvidencePacket,
        *,
        profile: str = "quick_insight",
        horizon: int | None = None,
    ) -> tuple[AIOpinion, dict[str, int] | None]:
        """Like insight() plus real provider token counts when available.

        Default: delegates to insight() and returns (opinion, None) so the
        router falls back to its len//4 estimate. Vendors with usage metadata
        override this to return {"prompt_tokens": n, "completion_tokens": m}.
        Never raises for usage-parse failures (returns None usage).
        """
        return await self.insight(packet, profile=profile, horizon=horizon), None

    def health(self) -> dict[str, Any]:
        """Safe for API responses: configuration only, never key material."""
        return {
            "provider": self.name,
            "model": self.model,
            "configured": self.is_configured(),
            "stub_mode": not self.is_configured(),
        }

    # -- helpers ---------------------------------------------------------
    def _stamp(self, opinion: AIOpinion) -> AIOpinion:
        opinion.provider = self.name
        opinion.model = self.model
        return opinion

    def _coerce_strict(self, raw: str | bytes | dict, *, horizon: int | None) -> AIOpinion:
        opinion = parse_opinion_strict(raw)
        if horizon in (5, 21, 63):
            opinion.time_horizon_days = horizon
        return self._stamp(opinion)

    def _stub(self, packet: EvidencePacket, reason: str, horizon: int | None) -> AIOpinion:
        return build_stub_opinion(
            packet, provider=self.name, model=self.model, horizon=horizon, reason=reason
        )

    @staticmethod
    def _redact_error(exc: BaseException) -> str:
        from backend.security.secrets import redact_string

        return redact_string(f"{type(exc).__name__}: {exc}")[:280]

    def _prompt_for(self, profile: str, packet: EvidencePacket, horizon: int | None) -> str:
        from backend.ai.prompts import render_prompt

        return render_prompt(profile, packet, horizon=horizon)

    @staticmethod
    def _finite_usage(value: object) -> int | None:
        try:
            n = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        if n < 0 or n > 10_000_000:
            return None
        return n

    async def _post_json_with_usage(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_s: float = 15.0,
        max_retries: int = 1,
        backoff_base_s: float = 0.4,
    ) -> tuple[str, dict | None]:
        """POST JSON and return (raw_text, raw_body_dict|None).

        Same retry/redaction contract as _post_json; the parsed body lets
        vendors extract real usageMetadata/usage without a second parse.
        """
        raw = await self._post_json(
            url, headers, payload,
            timeout_s=timeout_s, max_retries=max_retries,
            backoff_base_s=backoff_base_s,
        )
        try:
            import json as _json

            body = _json.loads(raw)
            if isinstance(body, dict):
                return raw, body
        except Exception:
            pass
        return raw, None

    async def _post_json(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_s: float = 15.0,
        max_retries: int = 1,
        backoff_base_s: float = 0.4,
    ) -> str:
        """POST JSON via httpx with exponential-backoff retries.

        - Retries ONLY transient failures (timeouts/connection errors,
          HTTP 429/5xx); 4xx (bad key/model/payload) fails fast.
        - Headers/payload (key material) are never logged; errors are
          redacted via _redact_error.
        - Backoff: backoff_base_s * 2**attempt (attempt 0-based), no jitter
          so tests stay deterministic.
        """
        import httpx

        attempts = max(1, int(max_retries) + 1)
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                async with httpx.AsyncClient(timeout=timeout_s) as client:
                    response = await client.post(url, headers=headers, json=payload)
            except Exception as exc:
                last_exc = exc
                if attempt < attempts - 1 and _is_transient_failure(None, exc):
                    await asyncio.sleep(backoff_base_s * (2**attempt))
                    continue
                raise RuntimeError(f"provider request failed: {self._redact_error(exc)}") from exc
            if response.status_code >= 400:
                transient = _is_transient_failure(response.status_code)
                if transient and attempt < attempts - 1:
                    await asyncio.sleep(backoff_base_s * (2**attempt))
                    continue
                # Include the error body (no key material in it — headers
                # are never logged). Turns opaque "HTTP 400" stubs into the
                # real cause: bad key, unknown model, billing, region.
                raise RuntimeError(f"provider HTTP {response.status_code}: {response.text[:180]}")
            return response.text
        if last_exc is not None:
            raise RuntimeError(f"provider request failed: {self._redact_error(last_exc)}") from last_exc
        raise RuntimeError("provider request failed: retries exhausted")
