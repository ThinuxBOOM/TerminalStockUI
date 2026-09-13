"""Audit router: versioned forecast log + AI decision log (M3/M4, spec Milestone 0 + Sec 6-7).

Prefix: /api/audit
  GET /api/audit/forecasts?symbol=AAPL  -> versioned forecast log
  GET /api/audit/ai_decisions           -> AI opinion log (provider/model/weight)

Append-only, hash-chained via AuditLog.compute_hash (backend/db/models.py).
Payloads are redacted before insert AND on read (defense in depth).
Audit logs never contain keys.

Wire-up (do NOT edit backend/api/main.py directly; document only):
    from backend.api.audit import router as audit_router
    app.include_router(audit_router)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from backend.db.models import AuditLog, Forecast, Instrument
from backend.db.session import get_db
from backend.security.secrets import redact_mapping

router = APIRouter(prefix="/api/audit", tags=["audit"])

DISCLOSURE = "Not investment advice. For informational purposes only."


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _created_iso(value: Any) -> str:
    """Canonical isoformat used both when appending and when verifying.

    SQLite round-trips DateTime(timezone=True) as naive; normalize naive
    timestamps as UTC so the hash material is stable across write/read.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    return str(value)


def _iso_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    return str(value)


def append_audit_log(
    db: Session,
    *,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: str,
    payload: Optional[dict] = None,
) -> AuditLog:
    """Append one hash-chained audit row. Redacts secrets before hashing/insert.

    Returns the persisted AuditLog (committed + refreshed).
    """
    redacted = redact_mapping(dict(payload or {}))
    last = db.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(1)).scalars().first()
    prev_hash = last.hash if last is not None else None
    now = _utcnow()
    created_iso = _created_iso(now)
    digest = AuditLog.compute_hash(prev_hash, created_iso, actor, action, entity_type, entity_id, redacted)
    row = AuditLog(
        created_at=now,
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        payload=redacted,
        prev_hash=prev_hash,
        hash=digest,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def log_forecast_created(
    db: Session,
    *,
    forecast_id: str,
    payload: Optional[dict] = None,
    actor: str = "system",
) -> AuditLog:
    """Convenience: audit event for a newly stored forecast version."""
    return append_audit_log(
        db,
        actor=actor,
        action="forecast.created",
        entity_type="forecast",
        entity_id=str(forecast_id),
        payload=payload or {},
    )


def log_ai_opinion(
    db: Session,
    *,
    opinion_id: str,
    provider: str,
    model: str,
    weight: float,
    extra: Optional[dict] = None,
    actor: str = "system",
) -> AuditLog:
    """Convenience: audit event for a bounded AI opinion (redacted)."""
    payload: dict = {"provider": provider, "model": model, "weight": weight}
    if extra:
        payload.update(extra)
    return append_audit_log(
        db,
        actor=actor,
        action="ai.opinion.requested",
        entity_type="ai_opinion",
        entity_id=str(opinion_id),
        payload=payload,
    )


def _forecast_to_out(f: Forecast, symbol: Optional[str] = None) -> dict:
    low = float(f.expected_ret_low) if f.expected_ret_low is not None else None
    high = float(f.expected_ret_high) if f.expected_ret_high is not None else None
    return {
        "forecast_id": str(f.forecast_id),
        "instrument_id": str(f.instrument_id),
        "symbol": symbol,
        "horizon_days": f.horizon_days,
        "target_date": f.target_date.isoformat() if f.target_date is not None else None,
        "direction_probability": float(f.direction_prob) if f.direction_prob is not None else None,
        "expected_return_range": [low, high] if low is not None and high is not None else None,
        "volatility_regime": f.volatility_regime,
        "drawdown_probability": float(f.drawdown_prob) if f.drawdown_prob is not None else None,
        "confidence": f.confidence,
        "model_version": f.model_version,
        "feature_version": f.feature_version,
        "data_version": f.data_version,
        "ai_provider": f.ai_provider,
        "ai_model": f.ai_model,
        "ai_weight": float(f.ai_weight) if f.ai_weight is not None else 0.0,
        "provenance": f.provenance or {},
        "created_at": _iso_or_none(f.created_at),
    }


def _ai_row_to_out(row: AuditLog) -> dict:
    payload = redact_mapping(row.payload or {})
    provider = payload.get("provider") or payload.get("ai_provider")
    model = payload.get("model") or payload.get("ai_model")
    weight = payload.get("weight", payload.get("ai_weight"))
    try:
        weight_f = float(weight) if weight is not None else None
    except (TypeError, ValueError):
        weight_f = None
    return {
        "id": row.id,
        "created_at": _iso_or_none(row.created_at),
        "actor": row.actor,
        "action": row.action,
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
        "provider": provider,
        "model": model,
        "weight": weight_f,
        "evidence_ids": payload.get("evidence_ids"),
        "payload": payload,
        "prev_hash": row.prev_hash,
        "hash": row.hash,
    }


@router.get("/forecasts")
def list_forecasts(
    symbol: Optional[str] = Query(default=None, description="Exchange symbol filter, e.g. AAPL"),
    instrument_id: Optional[str] = Query(default=None),
    horizon_days: Optional[int] = Query(default=None, description="One of 5, 21, 63"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    """Versioned forecast log. Every row carries model/feature/data versions + timestamp."""
    try:
        stmt = select(Forecast, Instrument).join(
            Instrument, Forecast.instrument_id == Instrument.instrument_id, isouter=True
        )
        if instrument_id:
            try:
                import uuid as _uuid

                stmt = stmt.where(Forecast.instrument_id == _uuid.UUID(str(instrument_id)))
            except (ValueError, AttributeError):
                stmt = stmt.where(cast(Forecast.instrument_id, String) == str(instrument_id))
                # NOTE: unreachable for valid UUIDs; kept as a portable fallback.
        if symbol:
            stmt = stmt.where(func.upper(Instrument.exchange_symbol) == symbol.upper())
        if horizon_days is not None:
            stmt = stmt.where(Forecast.horizon_days == horizon_days)
        stmt = stmt.order_by(Forecast.created_at.desc()).limit(limit).offset(offset)
        rows = db.execute(stmt).all()
        out = [_forecast_to_out(f, symbol=(inst.exchange_symbol if inst is not None else None)) for f, inst in rows]
        return {"forecasts": out, "count": len(out), "limit": limit, "offset": offset, "disclosure": DISCLOSURE}
    except Exception:
        # No tables yet (fresh dev DB without migrations) → empty log, not 500.
        return {"forecasts": [], "count": 0, "limit": limit, "offset": offset, "disclosure": DISCLOSURE}


@router.get("/ai_decisions")
def list_ai_decisions(
    provider: Optional[str] = Query(default=None, description="Filter by AI provider, e.g. gemini"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    """AI opinion log with provider/model/weight. Payloads are redacted; never contain keys."""
    try:
        stmt = (
            select(AuditLog)
            .where(or_(AuditLog.action.like("ai.%"), AuditLog.entity_type.in_(["ai_opinion", "ai_decision"])))
            .order_by(AuditLog.id.desc())
        )
        rows = db.execute(stmt).scalars().all()
        decisions = [_ai_row_to_out(r) for r in rows]
        if provider:
            decisions = [d for d in decisions if (d.get("provider") or "").lower() == provider.lower()]
        total = len(decisions)
        page = decisions[offset: offset + limit]
        return {"decisions": page, "count": len(page), "total": total, "limit": limit, "offset": offset, "disclosure": DISCLOSURE}
    except Exception:
        return {"decisions": [], "count": 0, "total": 0, "limit": limit, "offset": offset, "disclosure": DISCLOSURE}
