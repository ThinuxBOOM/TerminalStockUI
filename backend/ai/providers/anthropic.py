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
        prompt = self._prompt_for(profile, packet, horizon)
        key = self._load_api_key()  # decrypted here, at call time only
        if key is None:
            return self._stub(packet, "no API key configured", horizon)
        headers = {
            "Content-Type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        }
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 800,
            "temperature": 0.1,
            "system": "Return strict JSON only. This is not investment advice.",
            "messages": [{"role": "user", "content": prompt}],
        }
        key = ""
        try:
            text = await self._post_json("https://api.anthropic.com/v1/messages", headers, payload)
            return self._coerce_strict(_extract_anthropic_text(text), horizon=horizon)
        except ValueError as exc:
            return self._stub(packet, f"response failed validation: {exc}".strip()[:200], horizon)
        except Exception as exc:
            return self._stub(packet, f"live call unavailable ({self._redact_error(exc)})", horizon)


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
