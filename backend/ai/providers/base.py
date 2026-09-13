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

import time
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from backend.ai.schemas import AIOpinion, EvidencePacket, parse_opinion_strict

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
        return None

    def is_configured(self) -> bool:
        try:
            if bool(self._store().has(self.name, self.api_key_label)):
                return True
        except Exception:
            pass
        import os

        return bool(os.getenv(f"{self.name.upper()}_API_KEY", "").strip())

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

    async def _post_json(self, url: str, headers: dict[str, str], payload: dict[str, Any], timeout_s: float = 15.0) -> str:
        """POST JSON via httpx without ever logging headers/payload secrets."""
        import httpx

        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                response = await client.post(url, headers=headers, json=payload)
        except Exception as exc:
            raise RuntimeError(f"provider request failed: {self._redact_error(exc)}") from exc
        _ = time.monotonic() - started
        if response.status_code >= 400:
            # Include Google's error body (no key material in it — headers
            # are never logged). Turns opaque "HTTP 400" stubs into the real
            # cause: bad key, unknown model, billing, region, bad field.
            raise RuntimeError(f"provider HTTP {response.status_code}: {response.text[:180]}")
        return response.text
