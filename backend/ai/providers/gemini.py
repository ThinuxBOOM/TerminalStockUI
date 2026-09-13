"""Gemini provider (default for all M4 profiles). Low temperature, JSON-only."""

from __future__ import annotations

import json
from typing import Any

from backend.ai.providers.base import BaseProvider
from backend.ai.schemas import AIOpinion, EvidencePacket


class GeminiProvider(BaseProvider):
    name = "gemini"
    default_model = "gemini-3.7-flash"

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
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        headers = {"Content-Type": "application/json", "x-goog-api-key": key}
        payload: dict[str, Any] = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                # NOTE: no responseMimeType — application/json without a
                # responseSchema is rejected (HTTP 400) on current models.
                # Strictness is enforced client-side instead: prompts demand
                # JSON-only and parse_opinion_strict strips fences/prose.
                # 2048: thinking models share this budget with thinking
                # tokens; 800 risked truncation -> validation stub.
                "maxOutputTokens": 2048,
            },
        }
        key = ""  # drop plaintext reference ASAP; never logged/returned
        try:
            text = await self._post_json(url, headers, payload)
            raw_text = _extract_gemini_text(text)
            return self._coerce_strict(raw_text, horizon=horizon)
        except ValueError as exc:  # strict validation failure -> safe stub
            return self._stub(packet, f"response failed validation: {exc}".strip()[:200], horizon)
        except Exception as exc:  # network/HTTP/etc -> graceful stub, never crash
            return self._stub(packet, f"live call unavailable ({self._redact_error(exc)})", horizon)


def _extract_gemini_text(body: str) -> str:
    try:
        payload = json.loads(body)
        candidates = payload.get("candidates") or []
        parts = (candidates[0].get("content") or {}).get("parts") or []
        texts = [part.get("text", "") for part in parts if isinstance(part, dict)]
        joined = "\n".join(text for text in texts if text).strip()
        if joined:
            return joined
    except Exception:
        pass
    return body
