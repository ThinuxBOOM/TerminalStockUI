"""Evidence packet builder (Milestone 4).

build_evidence_packet(symbol, deterministic_outputs, provenance) assembles
the bounded context handed to AI providers.

Hard rules:
- NEVER includes raw candles/bars/OHLCV series, full statements, or secrets.
- Caps all sizes (lists truncated, strings clipped) so prompts stay bounded.
- Every packet carries limitations + an evidence_hash used as cache key.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from backend.ai.schemas import (
    MAX_EVENTS,
    MAX_LIMITATIONS,
    EvidenceItem,
    EvidenceMetadata,
    EvidencePacket,
    FreshnessInfo,
)

# Keys dropped at ANY nesting depth (raw market data / statements / secrets).
FORBIDDEN_EXACT = frozenset({
    "candles", "bars", "ohlcv", "closes", "prices", "price_history",
    "raw", "raw_candles", "raw_bars", "series", "history",
    "statements", "financial_statements", "full_statements", "filings_full",
    "api_key", "apikey", "secret", "token", "password", "authorization",
    "cookie", "set-cookie", "private_key", "client_secret",
})

_SENSITIVE_SUBSTRINGS = (
    "api_key", "apikey", "secret", "token", "password",
    "authorization", "cookie", "private",
)

_MAX_SUMMARY_KEYS = 20
_MAX_STR = 280
_MAX_DETAIL = 500

# ---------------------------------------------------------------------------
# Token budgets per profile (prompt-input caps, ~4 chars/token).
#
# Quick Insight ~300-600 tokens, Forecast Assist ~1000, Deep Research 4000
# max, Report ~2000. Enforced via truncation/summarization (top 5 bull/bear,
# cap events 20, no raw candles — raw series/secrets already stripped above).
# These keep AI calls cheap; prompts/__init__.render_prompt() truncates the
# packet JSON to budget*4 chars so no profile can blow its budget.
# ---------------------------------------------------------------------------
PROFILE_TOKEN_BUDGETS: dict[str, int] = {
    "quick_insight": 600,
    "forecast_assist": 1000,
    "deep_research": 4000,
    "report": 2000,
}

# Per-profile detail clipping (chars) — quick profiles summarize harder.
_PROFILE_DETAIL_CLIP: dict[str, int] = {
    "quick_insight": 140,
    "forecast_assist": 200,
    "deep_research": 500,
    "report": 280,
}

_PROFILE_SUMMARY_KEYS: dict[str, int] = {
    "quick_insight": 8,
    "forecast_assist": 12,
    "deep_research": 20,
    "report": 16,
}

_PROFILE_EVENT_CAP: dict[str, int] = {
    "quick_insight": 10,
    "forecast_assist": 12,
    "deep_research": 20,
    "report": 20,
}


def estimate_packet_tokens(packet: Any) -> int:
    """Rough input-token estimate for a packet (~4 chars/token)."""
    try:
        text = packet.model_dump_json() if hasattr(packet, "model_dump_json") else str(packet)
    except Exception:
        text = str(packet)
    return max(1, len(text) // 4)


def _is_forbidden_key(key: str) -> bool:
    lowered = key.strip().lower()
    if lowered in FORBIDDEN_EXACT:
        return True
    return any(part in lowered for part in _SENSITIVE_SUBSTRINGS)


def _clip(value: Any, limit: int) -> str:
    text = "" if value is None else str(value)
    return text.strip()[:limit]


def _sanitize(value: Any, depth: int = 0) -> Any:
    """Deep-copy with forbidden keys removed and sizes capped."""
    if depth > 6:
        return "[truncated: max depth]"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for raw_key in list(value.keys())[:50]:
            key = str(raw_key)[:120]
            if _is_forbidden_key(key):
                continue
            out[key] = _sanitize(value[raw_key], depth + 1)
            if len(out) >= _MAX_SUMMARY_KEYS:
                break
        return out
    if isinstance(value, (list, tuple)):
        return [_sanitize(item, depth + 1) for item in list(value)[:MAX_EVENTS]]
    if isinstance(value, str):
        return value.strip()[:_MAX_STR]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    try:
        return json.dumps(value, default=str)[:_MAX_STR]
    except Exception:
        return str(value)[:_MAX_STR]


def _as_items(entries: Any, prefix: str, cap: int) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    if not isinstance(entries, list):
        return items
    for index, entry in enumerate(entries[:cap]):
        if isinstance(entry, EvidenceItem):
            items.append(entry)
            continue
        if isinstance(entry, dict):
            label = _clip(entry.get("label") or entry.get("title") or entry.get("signal") or f"signal {index + 1}", 200)
            detail = _clip(entry.get("detail") or entry.get("description") or entry.get("reason") or "", _MAX_DETAIL)
            source = _clip(entry.get("source") or "deterministic", 120)
            item_id = _clip(entry.get("id") or f"{prefix}-{index + 1}", 120)
        else:
            label = _clip(entry or f"signal {index + 1}", 200)
            detail, source, item_id = "", "deterministic", f"{prefix}-{index + 1}"
        if not label:
            continue
        items.append(EvidenceItem(id=item_id or f"{prefix}-{index + 1}", label=label, detail=detail, source=source))
    return items


def _coerce_provenance(provenance: Any) -> tuple[str, FreshnessInfo, str]:
    """Return (source, freshness, quality_grade) from a Provenance/dict/None."""
    freshness = FreshnessInfo()
    source = "unknown"
    grade = "C"
    payload: dict[str, Any] = {}
    if provenance is None:
        return source, freshness, grade
    if hasattr(provenance, "model_dump"):
        try:
            payload = provenance.model_dump(mode="json")  # type: ignore[union-attr]
        except Exception:
            payload = {}
    elif isinstance(provenance, dict):
        payload = dict(provenance)
    try:
        source = _clip(payload.get("source") or source, 64) or "unknown"
        as_of = payload.get("as_of")
        as_of_dt: datetime | None = None
        if isinstance(as_of, datetime):
            as_of_dt = as_of
        elif isinstance(as_of, str) and as_of.strip():
            try:
                as_of_dt = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
            except ValueError:
                as_of_dt = None
        delay = int(payload.get("delay_minutes", 15))
        fallback = bool(payload.get("fallback_used", False))
        missing = [str(m)[:80] for m in (payload.get("missing_fields") or [])][:50]
        grade_raw = str(payload.get("quality_grade", "C")).strip().upper()
        grade = grade_raw if grade_raw in ("A", "B", "C", "D", "F") else "C"
        freshness = FreshnessInfo(
            as_of=as_of_dt, delay_minutes=max(0, delay),
            fallback_used=fallback, missing_fields=missing,
        )
    except Exception:
        pass
    return source, freshness, grade


def _hash_packet(core: dict[str, Any]) -> str:
    canonical = json.dumps(core, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_evidence_packet(
    symbol: str,
    deterministic_outputs: dict[str, Any] | None = None,
    provenance: Any | None = None,
    *,
    instrument: Any | None = None,
    events: list[Any] | None = None,
    as_of: datetime | None = None,
) -> EvidencePacket:
    """Build a bounded, sanitized evidence packet.

    Args:
        symbol: exchange symbol (upper-cased + stripped).
        deterministic_outputs: indicator/fundamental/forecast summaries.
            Raw candles, full statements, and secrets are stripped.
        provenance: Provenance model or dict (source/as_of/delay/grade).
        instrument: optional instrument model/dict (id/MIC/currency only).
        events: optional material events list (capped at 20).
        as_of: packet timestamp override (defaults to UTC now).
    """
    clean_symbol = (symbol or "").strip().upper()
    if not clean_symbol:
        raise ValueError("symbol must be a non-empty string")
    clean_symbol = clean_symbol[:32]

    raw_outputs = dict(deterministic_outputs or {})
    sanitized = _sanitize(raw_outputs)
    if not isinstance(sanitized, dict):
        sanitized = {}

    source, freshness, grade = _coerce_provenance(provenance)
    stamp = as_of or freshness.as_of or datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)

    instrument_id, mic, currency = "", "", "USD"
    if instrument is not None:
        try:
            data = instrument.model_dump() if hasattr(instrument, "model_dump") else dict(instrument)
            instrument_id = _clip(data.get("instrument_id") or "", 120)
            mic = _clip(data.get("exchange_mic") or data.get("trading_calendar") or "", 16)
            currency = _clip(data.get("currency") or "USD", 8) or "USD"
        except Exception:
            pass
    if not mic and isinstance(raw_outputs, dict):
        mic = _clip(raw_outputs.get("exchange_mic") or "", 16)

    metadata = EvidenceMetadata(
        symbol=clean_symbol, instrument_id=instrument_id,
        exchange_mic=mic, currency=currency, source=source, as_of=stamp,
    )

    deterministic_summary = {str(k)[:120]: v for k, v in list(sanitized.items())[:_MAX_SUMMARY_KEYS]}
    # Keep only summary-level keys; nested raw series were already stripped.
    for drop in ("bars", "candles", "ohlcv"):
        deterministic_summary.pop(drop, None)

    top_bullish = _as_items(sanitized.get("top_bullish", sanitized.get("bullish", [])), "bull", 5)
    top_risks = _as_items(sanitized.get("top_risks", sanitized.get("risks", sanitized.get("bearish", []))), "risk", 5)
    packet_events = _as_items(
        events if events is not None else sanitized.get("events", []), "ev", MAX_EVENTS
    )

    limitations: list[str] = [
        "Deterministic analytics are the source of truth; AI opinion is bounded and never overrides the quantitative core.",
        "Not investment advice. Verify all figures from primary sources before acting.",
    ]
    for extra in sanitized.get("limitations", []) if isinstance(sanitized.get("limitations"), list) else []:
        text = _clip(extra, _MAX_STR)
        if text and text not in limitations:
            limitations.append(text)
    freshness_note = (
        f"Data source {source}, quality grade {grade}, delay "
        f"{freshness.delay_minutes} min"
        + (" (fallback/cached data used)" if freshness.fallback_used else "")
        + (f"; missing: {', '.join(freshness.missing_fields[:5])}" if freshness.missing_fields else "")
        + "."
    )
    limitations.append(freshness_note)
    limitations = limitations[:MAX_LIMITATIONS]

    core = {
        "symbol": clean_symbol, "metadata": metadata.model_dump(mode="json"),
        "quality_grade": grade, "freshness": freshness.model_dump(mode="json"),
        "deterministic_summary": deterministic_summary,
        "top_bullish": [item.model_dump() for item in top_bullish],
        "top_risks": [item.model_dump() for item in top_risks],
        "events": [item.model_dump() for item in packet_events],
        "limitations": limitations,
    }
    # Hash covers evidence CONTENT only: wall-clock stamps (metadata.as_of,
    # freshness.as_of) are excluded so identical evidence reuses the cached
    # response instead of missing on every timestamp tick.
    hash_core = json.loads(json.dumps(core, default=str))
    try:
        hash_core["metadata"].pop("as_of", None)
        hash_core["freshness"].pop("as_of", None)
    except (KeyError, AttributeError, TypeError):
        pass
    digest = _hash_packet(hash_core)
    return EvidencePacket(
        packet_id=f"{clean_symbol}-{digest[:12]}",
        symbol=clean_symbol, evidence_hash=digest, created_at=stamp,
        metadata=metadata, quality_grade=grade, freshness=freshness,  # type: ignore[arg-type]
        deterministic_summary=deterministic_summary,
        top_bullish=top_bullish, top_risks=top_risks,
        events=packet_events, limitations=limitations,
    )


def _normalize_profile_key(profile: str | None) -> str:
    key = (profile or "").strip().lower().replace(" ", "_").replace("-", "_")
    return key if key in PROFILE_TOKEN_BUDGETS else "quick_insight"


def summarize_packet_for_profile(packet: EvidencePacket, profile: str) -> dict[str, Any]:
    """Return a budget-truncated dict view of a packet for prompt rendering.

    - Top 5 bull / top 5 bear enforced (schema caps); events capped per
      profile (quick 10, forecast 12, deep/report 20).
    - Detail strings clipped per profile (quick 140 chars … deep 500).
    - deterministic_summary keys capped per profile (quick 8 … deep 20).
    - Never includes raw candles/statements/secrets (already stripped at
      build time; this only shrinks further).
    """
    key = _normalize_profile_key(profile)
    detail_clip = _PROFILE_DETAIL_CLIP[key]
    max_summary_keys = _PROFILE_SUMMARY_KEYS[key]
    event_cap = _PROFILE_EVENT_CAP[key]

    def _shrink_item(item: Any) -> dict[str, Any]:
        try:
            data = item.model_dump() if hasattr(item, "model_dump") else dict(item)
        except Exception:
            return {"id": "ev-?", "label": str(item)[:140], "detail": "", "source": ""}
        return {
            "id": str(data.get("id", ""))[:120],
            "label": str(data.get("label", ""))[:140],
            "detail": str(data.get("detail", ""))[:detail_clip],
            "source": str(data.get("source", ""))[:120],
        }

    summary: dict[str, Any] = {}
    try:
        raw_summary = packet.deterministic_summary or {}
    except Exception:
        raw_summary = {}
    for name in list(raw_summary.keys())[:max_summary_keys]:
        try:
            text = json.dumps(raw_summary[name], default=str)
        except Exception:
            text = str(raw_summary[name])
        summary[str(name)[:120]] = text[:detail_clip]

    return {
        "packet_id": packet.packet_id,
        "symbol": packet.symbol,
        "evidence_hash": packet.evidence_hash,
        "quality_grade": packet.quality_grade,
        "freshness": packet.freshness.model_dump(mode="json") if hasattr(packet.freshness, "model_dump") else {},
        "deterministic_summary": summary,
        "top_bullish": [_shrink_item(i) for i in (packet.top_bullish or [])[:5]],
        "top_risks": [_shrink_item(i) for i in (packet.top_risks or [])[:5]],
        "events": [_shrink_item(i) for i in (packet.events or [])[:event_cap]],
        "limitations": [str(x)[:detail_clip] for x in (packet.limitations or [])[:MAX_LIMITATIONS]],
    }


def packet_prompt_json(packet: EvidencePacket, profile: str, max_tokens: int | None = None) -> str:
    """Serialize a packet for prompts, hard-truncated to a token budget.

    Defaults to PROFILE_TOKEN_BUDGETS[profile]; truncation appends a marker
    so the model knows input was summarized (never silently dropped).
    """
    key = _normalize_profile_key(profile)
    budget_tokens = int(max_tokens or PROFILE_TOKEN_BUDGETS[key])
    budget_chars = max(512, budget_tokens * 4)
    view = summarize_packet_for_profile(packet, key)
    try:
        text = json.dumps(view, default=str, indent=1)
    except Exception:
        text = str(view)[:budget_chars]
    if len(text) > budget_chars:
        text = text[:budget_chars] + '\n  "...truncated": true\n}'
    return text
