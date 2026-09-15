"""Best-effort DB writers for revamp tables (Agent 7 completion).

All helpers are total: missing tables, closed DBs, CHECK violations, or a
None session degrade to a logged no-op instead of raising, so hot paths
(quotes, AI insight, health probes) never 500 on a pre-0006 database.

Tables (mirrors infra/migrations/0006_revamp.sql):
- ai_token_ledger (Agent 4 token accounting)
- provider_health_history (Agent 6 durable probe history)
- indicator_cache (Agent 5 deterministic overlay cache)

Future auth hooks (user_id/tier) are accepted and stored but NEVER enforced
here — enforcement lands with the users DB + subscription router.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_VALID_PROVIDERS = ("gemini", "openai", "anthropic", "xai")
_VALID_CALL_TYPES = ("opinion", "evidence", "forecast", "embedding", "other")
_PROFILE_TO_CALL_TYPE = {
    "quick_insight": "opinion",
    "forecast_assist": "forecast",
    "deep_research": "evidence",
    "report": "forecast",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _call_type_for(profile: str | None, call_type: str | None) -> str:
    if call_type:
        cand = str(call_type).strip().lower()
        if cand in _VALID_CALL_TYPES:
            return cand
    if profile:
        mapped = _PROFILE_TO_CALL_TYPE.get(str(profile).strip().lower())
        if mapped:
            return mapped
    return "other"


def log_ai_tokens(
    db: Any,
    *,
    provider: str,
    model: str = "",
    profile: str = "quick_insight",
    call_type: str | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    latency_ms: int | None = None,
    evidence_hash: str | None = None,
    user_id: Any = None,
    tier: str | None = None,
) -> bool:
    """Append one ai_token_ledger row. Returns True on persist, False on skip."""
    if db is None:
        return False
    prov = str(provider or "").strip().lower()
    if prov not in _VALID_PROVIDERS:
        return False
    try:
        from backend.db.models import AiTokenLedger
    except Exception:
        return False
    try:
        ct = _call_type_for(profile, call_type)
        in_tok = max(0, int(input_tokens or 0))
        out_tok = max(0, int(output_tokens or 0))
        lat = None
        if latency_ms is not None:
            try:
                lat = max(0, int(float(latency_ms)))
            except (TypeError, ValueError):
                lat = None
        row = AiTokenLedger(
            provider=prov,
            model=str(model or "")[:128],
            call_type=ct,
            input_tokens=in_tok,
            output_tokens=out_tok,
            latency_ms=lat,
            evidence_hash=(str(evidence_hash)[:128] if evidence_hash else None),
            user_id=user_id,
            tier=(str(tier).strip().lower()[:16] if tier else None),
        )
        db.add(row)
        db.commit()
        return True
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        logger.debug("log_ai_tokens skipped: %s", type(exc).__name__)
        return False


def record_provider_health(
    db: Any,
    *,
    provider: str,
    ok: bool,
    latency_ms: int | None = None,
    error_code: str | None = None,
    ts: datetime | None = None,
) -> bool:
    """Append one provider_health_history row. Never raises."""
    if db is None:
        return False
    name = str(provider or "").strip().lower()
    if not name:
        return False
    try:
        from backend.db.models import ProviderHealthHistory
    except Exception:
        return False
    try:
        lat = None
        if latency_ms is not None:
            try:
                lat = max(0, int(float(latency_ms)))
            except (TypeError, ValueError):
                lat = None
        row = ProviderHealthHistory(
            provider=name,
            ts=ts or _utcnow(),
            ok=bool(ok),
            latency_ms=lat,
            error_code=(str(error_code)[:128] if error_code else None),
        )
        db.add(row)
        db.commit()
        return True
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        logger.debug("record_provider_health skipped: %s", type(exc).__name__)
        return False


def put_indicator(
    db: Any,
    *,
    instrument_id: Any,
    timeframe: str = "1d",
    indicator_key: str,
    payload: dict,
) -> bool:
    """Upsert one indicator_cache row (deterministic, not user-scoped)."""
    if db is None or instrument_id is None or not indicator_key:
        return False
    try:
        from backend.db.models import IndicatorCache
    except Exception:
        return False
    try:
        row = IndicatorCache(
            instrument_id=instrument_id,
            timeframe=str(timeframe or "1d")[:8],
            indicator_key=str(indicator_key)[:128],
            payload=dict(payload or {}),
            updated_at=_utcnow(),
        )
        db.merge(row)
        db.commit()
        return True
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        logger.debug("put_indicator skipped: %s", type(exc).__name__)
        return False


def get_indicator(db: Any, *, instrument_id: Any, timeframe: str = "1d", indicator_key: str) -> dict | None:
    """Fetch one cached indicator payload or None (miss/error). Never raises."""
    if db is None or instrument_id is None or not indicator_key:
        return None
    try:
        from backend.db.models import IndicatorCache

        row = (
            db.query(IndicatorCache)
            .filter(
                IndicatorCache.instrument_id == instrument_id,
                IndicatorCache.timeframe == str(timeframe or "1d"),
                IndicatorCache.indicator_key == str(indicator_key),
            )
            .first()
        )
        if row is None:
            return None
        return dict(row.payload or {})
    except Exception:
        return None


__all__ = [
    "get_indicator",
    "log_ai_tokens",
    "put_indicator",
    "record_provider_health",
]
