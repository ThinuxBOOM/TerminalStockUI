"""xAI (Grok) provider. Low temperature, JSON-only, graceful stub."""

from __future__ import annotations

import json
from typing import Any

from backend.ai.providers.base import BaseProvider
from backend.ai.schemas import AIOpinion, EvidencePacket


class XAIProvider(BaseProvider):
    name = "xai"
    default_model = "grok-3-mini"

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
            timeout_for_profile,
        )

        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
        payload: dict[str, Any] = {
            "model": self.model,
            "temperature": 0.1,
            "max_tokens": max_output_tokens_for_profile(profile),
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "Return strict JSON only. This is not investment advice."},
                {"role": "user", "content": prompt},
            ],
        }
        key = ""
        try:
            text, body = await self._post_json_with_usage(
                "https://api.x.ai/v1/chat/completions", headers, payload,
                timeout_s=timeout_for_profile(profile),
                max_retries=1, backoff_base_s=0.4,
            )
            return self._coerce_strict(_extract_xai_text(text), horizon=horizon), _extract_xai_usage(body)
        except ValueError as exc:
            return self._stub(packet, f"response failed validation: {exc}".strip()[:200], horizon), None
        except Exception as exc:
            return self._stub(packet, f"live call unavailable ({self._redact_error(exc)})", horizon), None


def _extract_xai_text(body: str) -> str:
    try:
        payload = json.loads(body)
        choices = payload.get("choices") or []
        content = ((choices[0].get("message") or {}) if choices else {}).get("content", "")
        if isinstance(content, str) and content.strip():
            return content
    except Exception:
        pass
    return body


def _extract_xai_usage(body: dict | None) -> dict[str, int] | None:
    """Real usage counts (prompt_tokens/completion_tokens, OpenAI-compatible)."""
    try:
        if not isinstance(body, dict):
            return None
        usage = body.get("usage") or {}
        if not isinstance(usage, dict):
            return None
        from backend.ai.providers.base import BaseProvider as _BP

        prompt = _BP._finite_usage(usage.get("prompt_tokens"))
        comp = _BP._finite_usage(usage.get("completion_tokens"))
        if prompt is None and comp is None:
            return None
        return {"prompt_tokens": prompt or 0, "completion_tokens": comp or 0}
    except Exception:
        return None
