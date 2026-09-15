"""Strict Pydantic schemas for the M4/M5 AI framework.

Spec (Milestones 4-5):
- EvidencePacket {metadata, quality/freshness, deterministic_summary,
  top_bullish[5], top_risks[5], events, limitations}.
  No raw candles, full statements, or keys (enforced in evidence.py).
- AIOpinion {direction bullish|bearish|neutral, probability 0-1,
  time_horizon_days 5|21|63 only, catalysts, risks, evidence_ids,
  limitations}. Validators reject bad probs/horizons/claims without
  evidence_ids.

Malformed JSON fails safe via parse_opinion_strict() (raises ValueError,
never returns a partial opinion).
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

Direction = Literal["bullish", "bearish", "neutral"]
ALLOWED_HORIZONS = (5, 21, 63)

DISCLAIMER = (
    "Not investment advice. This AI opinion is a bounded, evidence-grounded "
    "assistant output only and never overrides the deterministic quantitative "
    "forecast. Verify all figures from primary sources before acting."
)

MAX_BULLISH = 5
MAX_RISKS = 5
MAX_EVENTS = 20
MAX_LIMITATIONS = 10
MAX_STR = 500
MAX_SHORT_STR = 280


def _strict_horizon(value: Any) -> int:
    """Coerce to an allowed horizon (5/21/63). Rejects truncation traps
    like 21.5, bools, and non-numeric strings instead of silent int()."""
    if isinstance(value, bool):
        raise ValueError("time_horizon_days must be one of 5, 21, 63")
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError("time_horizon_days must be one of 5, 21, 63")
        value = int(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text.lstrip("+-").isdigit():
            raise ValueError("time_horizon_days must be one of 5, 21, 63")
        try:
            value = int(text)
        except ValueError as exc:
            raise ValueError("time_horizon_days must be one of 5, 21, 63") from exc
    elif isinstance(value, int):
        pass
    else:
        raise ValueError("time_horizon_days must be one of 5, 21, 63")
    if value not in ALLOWED_HORIZONS:
        raise ValueError("time_horizon_days must be one of 5, 21, 63")
    return value


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _clip_text(value: Any, limit: int = MAX_STR) -> str:
    text = "" if value is None else str(value)
    text = text.strip()
    return text[:limit]


# ---------------------------------------------------------------------------
# Evidence packet
# ---------------------------------------------------------------------------


class EvidenceItem(BaseModel):
    """One grounded evidence bullet referenced by id from AIOpinion."""

    id: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=200)
    detail: str = Field(default="", max_length=MAX_STR)
    source: str = Field(default="", max_length=120)

    @field_validator("id", "label", "detail", "source", mode="before")
    @classmethod
    def _strip(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class EvidenceMetadata(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    instrument_id: str = Field(default="", max_length=120)
    exchange_mic: str = Field(default="", max_length=16)
    currency: str = Field(default="USD", max_length=8)
    source: str = Field(default="unknown", max_length=64)
    as_of: datetime | None = None

    @field_validator("symbol", mode="before")
    @classmethod
    def _upper(cls, value: Any) -> Any:
        return value.strip().upper() if isinstance(value, str) else value


class FreshnessInfo(BaseModel):
    as_of: datetime | None = None
    delay_minutes: int = Field(default=15, ge=0, le=60 * 24 * 7)
    fallback_used: bool = False
    missing_fields: list[str] = Field(default_factory=list, max_length=50)


class EvidencePacket(BaseModel):
    """Bounded context handed to AI providers. Never contains raw candles,
    full statements, or secrets (see backend/ai/evidence.py)."""

    packet_id: str = Field(min_length=1, max_length=120)
    symbol: str = Field(min_length=1, max_length=32)
    evidence_hash: str = Field(min_length=8, max_length=128)
    created_at: datetime = Field(default_factory=_utcnow)
    metadata: EvidenceMetadata
    quality_grade: Literal["A", "B", "C", "D", "F"] = "C"
    freshness: FreshnessInfo = Field(default_factory=FreshnessInfo)
    deterministic_summary: dict[str, Any] = Field(default_factory=dict)
    top_bullish: list[EvidenceItem] = Field(default_factory=list, max_length=MAX_BULLISH)
    top_risks: list[EvidenceItem] = Field(default_factory=list, max_length=MAX_RISKS)
    events: list[EvidenceItem] = Field(default_factory=list, max_length=MAX_EVENTS)
    limitations: list[str] = Field(min_length=1, max_length=MAX_LIMITATIONS)

    @field_validator("symbol", mode="before")
    @classmethod
    def _upper_symbol(cls, value: Any) -> Any:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("limitations", mode="before")
    @classmethod
    def _clip_limitations(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        return [_clip_text(item) for item in value][:MAX_LIMITATIONS]

    @field_validator("deterministic_summary", mode="before")
    @classmethod
    def _cap_summary(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return {}
        out: dict[str, Any] = {}
        for key in list(value.keys())[:20]:
            raw = value[key]
            try:
                text = json.dumps(raw, default=str)
            except Exception:
                text = str(raw)
            out[str(key)[:120]] = text[:MAX_STR]
        return out

    @property
    def evidence_ids(self) -> list[str]:
        ids: list[str] = []
        for item in (*self.top_bullish, *self.top_risks, *self.events):
            if item.id not in ids:
                ids.append(item.id)
        return ids


# ---------------------------------------------------------------------------
# AI opinion (bounded)
# ---------------------------------------------------------------------------


class AIOpinion(BaseModel):
    """Bounded AI forecast opinion. Quantitative core always owns the final
    forecast; see backend/ai/blend.py for the fixed blend policy."""

    direction: Direction
    probability: float
    time_horizon_days: int
    catalysts: list[str] = Field(default_factory=list, max_length=MAX_BULLISH)
    risks: list[str] = Field(default_factory=list, max_length=MAX_RISKS)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    limitations: list[str] = Field(min_length=1, max_length=MAX_LIMITATIONS)
    provider: str = Field(default="unknown", max_length=64)
    model: str = Field(default="unknown", max_length=120)
    stub: bool = False
    disclaimer: str = Field(default=DISCLAIMER, max_length=1000)

    @field_validator("probability", mode="before")
    @classmethod
    def _check_probability(cls, value: Any) -> float:
        try:
            prob = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("probability must be a number in [0, 1]") from exc
        if not math.isfinite(prob) or not 0.0 <= prob <= 1.0:
            raise ValueError("probability must be a finite number in [0, 1]")
        return prob

    @field_validator("time_horizon_days", mode="before")
    @classmethod
    def _check_horizon(cls, value: Any) -> int:
        return _strict_horizon(value)

    @field_validator("catalysts", "risks", mode="before")
    @classmethod
    def _clip_bullets(cls, value: Any) -> Any:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("catalysts/risks must be lists of strings")
        return [_clip_text(item, MAX_SHORT_STR) for item in value][:MAX_BULLISH]

    @field_validator("evidence_ids", mode="before")
    @classmethod
    def _clean_evidence_ids(cls, value: Any) -> Any:
        if not isinstance(value, list) or len(value) == 0:
            raise ValueError("evidence_ids must be a non-empty list of evidence IDs")
        seen: list[str] = []
        for item in value:
            text = item.strip() if isinstance(item, str) else ""
            if text and text not in seen:
                seen.append(text[:120])
        if not seen:
            raise ValueError("evidence_ids must be a non-empty list of evidence IDs")
        return seen[:20]

    @field_validator("limitations", mode="before")
    @classmethod
    def _clean_limitations(cls, value: Any) -> Any:
        if not isinstance(value, list) or len(value) == 0:
            raise ValueError("limitations must be a non-empty list")
        return [_clip_text(item) for item in value][:MAX_LIMITATIONS]

    @model_validator(mode="after")
    def _require_grounding(self) -> AIOpinion:
        # evidence_ids non-empty is already enforced; this rejects the
        # "claims without evidence" shape explicitly with a clear message.
        if (self.catalysts or self.risks) and not self.evidence_ids:
            raise ValueError("catalysts/risks require non-empty evidence_ids")
        if not self.limitations:
            raise ValueError("limitations must be a non-empty list")
        return self


# ---------------------------------------------------------------------------
# Strict parsing (malformed JSON fails safe)
# ---------------------------------------------------------------------------


def _extract_json_object(text: str) -> str:
    """Extract the first {...} JSON object (strips markdown fences/prose)."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        lines = [line for line in lines if not line.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON object found in model response")
    return cleaned[start : end + 1]


def parse_opinion_strict(raw: str | bytes | dict[str, Any]) -> AIOpinion:
    """Parse and validate a raw model response. Raises ValueError on any
    malformed JSON or schema violation (callers fall back to a marked stub)."""
    if isinstance(raw, dict):
        try:
            return AIOpinion.model_validate(raw)
        except Exception as exc:
            raise ValueError(f"invalid AIOpinion payload: {exc}") from exc
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except Exception as exc:
            raise ValueError(f"cannot decode model response bytes: {exc}") from exc
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("empty model response")
    try:
        candidate = _extract_json_object(raw)
        payload = json.loads(candidate)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"malformed JSON in model response: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("model response JSON must be an object")
    try:
        return AIOpinion.model_validate(payload)
    except Exception as exc:
        raise ValueError(f"invalid AIOpinion payload: {exc}") from exc


# ---------------------------------------------------------------------------
# API request shapes (shared by backend/api/ai.py)
# ---------------------------------------------------------------------------


class TokenUsage(BaseModel):
    """Billing-grade token accounting per AI call (redacted, no key material).

    Logged by AIRouter._log_tokens + TokenLedger for future per-tier quota
    enforcement. All fields optional-tolerant so old entries still parse.
    """

    provider: str = Field(default="unknown", max_length=64)
    model: str = Field(default="unknown", max_length=120)
    profile: str = Field(default="quick_insight", max_length=64)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    cached: bool = False
    stub: bool = False
    # Tier stubs (future billing; never enforced today).
    user_tier: str | None = Field(default=None, max_length=32)
    call_type: str | None = Field(default=None, max_length=64)
    token_credits: int | None = Field(default=None)


class InsightRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    profile: str = Field(default="quick_insight", max_length=64)
    # --- future tier-routing stubs (accepted + logged, NEVER enforced) ---
    # Free: 20/day Quick only / Silver: 1000+400+100 / Gold: +Grok /
    # Platinum: unlimited+Deep. Enforcement lands later; today any value
    # (or none) behaves identically.
    user_tier: str | None = Field(default=None, max_length=32)
    call_type: str | None = Field(default=None, max_length=64)
    token_credits: int | None = Field(default=None)

    @field_validator("symbol", mode="before")
    @classmethod
    def _upper(cls, value: Any) -> Any:
        return value.strip().upper() if isinstance(value, str) else value


class ForecastOpinionRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    horizon: int = Field(default=21)
    profile: str = Field(default="forecast_assist", max_length=64)
    quant_prob: float | None = Field(default=None)
    ai_weight: float | None = Field(default=None)
    ai_enabled: bool = True
    # --- future tier-routing stubs (accepted + logged, NEVER enforced) ---
    user_tier: str | None = Field(default=None, max_length=32)
    call_type: str | None = Field(default=None, max_length=64)
    token_credits: int | None = Field(default=None)

    @field_validator("symbol", mode="before")
    @classmethod
    def _upper(cls, value: Any) -> Any:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("horizon", mode="before")
    @classmethod
    def _check_horizon(cls, value: Any) -> int:
        try:
            return _strict_horizon(value)
        except ValueError as exc:
            raise ValueError("horizon must be one of 5, 21, 63") from exc

    @field_validator("quant_prob", mode="before")
    @classmethod
    def _check_quant(cls, value: Any) -> Any:
        if value is None:
            return None
        try:
            prob = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("quant_prob must be a number in [0, 1]") from exc
        if not math.isfinite(prob) or not 0.0 <= prob <= 1.0:
            raise ValueError("quant_prob must be a finite number in [0, 1]")
        return prob
