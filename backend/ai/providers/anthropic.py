"""Anthropic (Claude) provider. Low temperature, JSON-only, graceful stub."""

from __future__ import annotations

import json
from typing import Any

from backend.ai.providers.base import BaseProvider
from backend.ai.schemas import AIOpinion, EvidencePacket


class AnthropicProvider(BaseProvider):
    name = "anthropic"
    default_model = "claude-3-5-haiku-latest"

    async def insight(
        self,
        packet: EvidencePacket,
        *,
        profile: str = "quick_insight",
        horizon: int | None = None,
    ) -> AIOpinion:
        opinion, _ = await self.insight_with_usage(packet, profile=profile, horizon=horizon)
        return opinion

    async def insight_with_usage(
        self,
        packet: EvidencePacket,
        *,
        profile: str = "quick_insight",
        horizon: int | None = None,
    ) -> tuple[AIOpinion, dict[str, int] | None]:
        prompt = self._prompt_for(profile, packet, horizon)
        key = self._load_api_key()  # decrypted here, at call time only
        if key is None:
            return self._stub(packet, "no API key configured", horizon), None
        from backend.ai.providers.base import (
            max_output_tokens_for_profile,
            prompt_cache_hint,
            timeout_for_profile,
        )

        _cache_hint = prompt_cache_hint("anthropic", profile)  # hook: ephemeral cache_control, no-op today
        headers = {
            "Content-Type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        }
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_output_tokens_for_profile(profile),
            "temperature": 0.1,
            # Prompt-caching hook (future): attach _cache_hint["cache_control"]
            # to the system block once billing enables it. Shape unchanged today.
            "system": "Return strict JSON only. This is not investment advice.",
            "messages": [{"role": "user", "content": prompt}],
        }
        key = ""
        try:
            text, body = await self._post_json_with_usage(
                "https://api.anthropic.com/v1/messages", headers, payload,
                timeout_s=timeout_for_profile(profile),
                max_retries=1, backoff_base_s=0.4,
            )
            return self._coerce_strict(_extract_anthropic_text(text), horizon=horizon), _extract_anthropic_usage(body)
        except ValueError as exc:
            return self._stub(packet, f"response failed validation: {exc}".strip()[:200], horizon), None
        except Exception as exc:
            return self._stub(packet, f"live call unavailable ({self._redact_error(exc)})", horizon), None


def _extract_anthropic_text(body: str) -> str:
    try:
        payload = json.loads(body)
        blocks = payload.get("content") or []
        texts = [b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
        joined = "\n".join(t for t in texts if t).strip()
        if joined:
            return joined
    except Exception:
        pass
    return body


def _extract_anthropic_usage(body: dict | None) -> dict[str, int] | None:
    """Real usage counts (input_tokens/output_tokens)."""
    try:
        if not isinstance(body, dict):
            return None
        usage = body.get("usage") or {}
        if not isinstance(usage, dict):
            return None
        from backend.ai.providers.base import BaseProvider as _BP

        prompt = _BP._finite_usage(usage.get("input_tokens"))
        comp = _BP._finite_usage(usage.get("output_tokens"))
        if prompt is None and comp is None:
            return None
        return {"prompt_tokens": prompt or 0, "completion_tokens": comp or 0}
    except Exception:
        return None
