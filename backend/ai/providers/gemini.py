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
                # Per-profile output cap (token-efficient; deep allows more).
                "maxOutputTokens": max(256, min(2048, max_output_tokens_for_profile(profile))),
            },
        }
        key = ""  # drop plaintext reference ASAP; never logged/returned
        try:
            text, body = await self._post_json_with_usage(
                url, headers, payload,
                timeout_s=timeout_for_profile(profile),
                max_retries=1, backoff_base_s=0.4,
            )
            raw_text = _extract_gemini_text(text)
            return self._coerce_strict(raw_text, horizon=horizon), _extract_gemini_usage(body)
        except ValueError as exc:  # strict validation failure -> safe stub
            return self._stub(packet, f"response failed validation: {exc}".strip()[:200], horizon), None
        except Exception as exc:  # network/HTTP/etc -> graceful stub, never crash
            return self._stub(packet, f"live call unavailable ({self._redact_error(exc)})", horizon), None


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


def _extract_gemini_usage(body: dict | None) -> dict[str, int] | None:
    """Real usageMetadata counts (promptTokenCount/candidatesTokenCount)."""
    try:
        if not isinstance(body, dict):
            return None
        meta = body.get("usageMetadata") or {}
        if not isinstance(meta, dict):
            return None
        from backend.ai.providers.base import BaseProvider as _BP

        prompt = _BP._finite_usage(meta.get("promptTokenCount"))
        comp = _BP._finite_usage(
            meta.get("candidatesTokenCount", meta.get("candidates_token_count"))
        )
        if prompt is None and comp is None:
            return None
        return {"prompt_tokens": prompt or 0, "completion_tokens": comp or 0}
    except Exception:
        return None
