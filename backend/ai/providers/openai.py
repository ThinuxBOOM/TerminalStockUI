"""OpenAI provider. Low temperature, JSON-only, graceful stub without a key."""

from __future__ import annotations

import json
from typing import Any

from backend.ai.providers.base import BaseProvider
from backend.ai.schemas import AIOpinion, EvidencePacket


class OpenAIProvider(BaseProvider):
    name = "openai"
    default_model = "gpt-4o-mini"

    async def insight(
        self,
        packet: EvidencePacket,
        *,
        profile: str = "quick_insight",
        horizon: int | None = None,
    ) -> AIOpinion:
        prompt = self._prompt_for(profile, packet, horizon)
        key = self._load_api_key()  # decrypted here, at call time only
        if key is None:
            return self._stub(packet, "no API key configured", horizon)
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
        payload: dict[str, Any] = {
            "model": self.model,
            "temperature": 0.1,
            "max_tokens": 800,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "Return strict JSON only. This is not investment advice."},
                {"role": "user", "content": prompt},
            ],
        }
        key = ""
        try:
            text = await self._post_json("https://api.openai.com/v1/chat/completions", headers, payload)
            return self._coerce_strict(_extract_openai_text(text), horizon=horizon)
        except ValueError as exc:
            return self._stub(packet, f"response failed validation: {exc}".strip()[:200], horizon)
        except Exception as exc:
            return self._stub(packet, f"live call unavailable ({self._redact_error(exc)})", horizon)


def _extract_openai_text(body: str) -> str:
    try:
        payload = json.loads(body)
        choices = payload.get("choices") or []
        content = ((choices[0].get("message") or {}) if choices else {}).get("content", "")
        if isinstance(content, str) and content.strip():
            return content
    except Exception:
        pass
    return body
