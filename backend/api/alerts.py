"""Alert rules CRUD + evaluation core (Phase 3b).

CRUD router (prefix ``/api/alerts``):

  POST   /api/alerts/            create {symbol, condition, threshold,
                              horizon_days?, target_ccy?}
  GET    /api/alerts/            list (?active_only=true filters to active)
  PATCH  /api/alerts/{alert_id}  subset {is_active, threshold, cooldown_hours}
  DELETE /api/alerts/{alert_id}  204 (cascades fired events)

``condition`` is one of price_above / price_below / direction_above /
direction_below / change_pct_below. ``horizon_days`` (5/21/63, default 21)
is consumed by the direction_* conditions only. Create validates the symbol
via the instrument registry (unknown -> 422) and rejects non-finite
thresholds (NaN/Inf -> 422).

Evaluation core :func:`evaluate_due_alerts` is shared by
``POST/GET /api/cron/evaluate`` and the ``evaluate_alerts`` worker job:
for each active alert due (never fired OR now - last_fired_at >= cooldown)
it observes the value (price/change_pct via the quote service for price_*;
direction_probability via ForecastService for direction_*), fires when the
condition is met (above: observed >= threshold; below: observed <=
threshold) by inserting an alert_events row + a redacted audit event +
updating last_fired_at, and ALWAYS attempts the delivery hook — delivery
failure never blocks firing. Thin history (insufficient bars) is a per-alert
``errors`` entry, never a batch 500.

Every response carries the standard provenance envelope plus the
"Not investment advice" disclosure.
"""

from __future__ import annotations

import logging
import math
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import String as SAString
from sqlalchemy import cast
from sqlalchemy.orm import Session

from backend.api.deps import get_market_service, get_registry
from backend.db.models import Alert, AlertEvent
from backend.db.session import get_db
from backend.forecasting.common import FORECAST_HORIZONS
from backend.instruments.registry import InstrumentRegistry
from backend.market_data.provenance import build_provenance
from backend.market_data.quality import grade_quality

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/alerts", tags=["alerts"])

DISCLOSURE = "Not investment advice. For informational purposes only."

AlertCondition = Literal[
    "price_above", "price_below",
    "direction_above", "direction_below",
    "change_pct_below",
]
ALERT_CONDITIONS = (
    "price_above", "price_below",
    "direction_above", "direction_below",
    "change_pct_below",
)
DIRECTION_CONDITIONS = frozenset({"direction_above", "direction_below"})

PROVENANCE_KEYS = {
    "source", "as_of", "delay_minutes", "quality_grade",
    "fallback_used", "missing_fields",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _alert_provenance(has_errors: bool = False) -> dict:
    now = _utcnow()
    try:
        grade, _ = grade_quality(
            delay_minutes=15, age_minutes=0.0, missing_fields=[],
            fallback_used=has_errors, reconciled=False,
        )
    except Exception:
        grade = "C" if has_errors else "B"
    return build_provenance(
        "alerts", as_of=now, delay_minutes=15, quality_grade=grade,
        fallback_used=has_errors, missing_fields=[],
    ).model_dump(mode="json")


def _iso_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    return str(value)


def _alert_to_out(row: Alert) -> dict:
    return {
        "alert_id": str(row.alert_id),
        "symbol": row.symbol,
        "exchange_mic": row.exchange_mic,
        "condition": row.condition,
        "threshold": float(row.threshold) if row.threshold is not None else None,
        "horizon_days": int(row.horizon_days),
        "target_ccy": row.target_ccy,
        "is_active": bool(row.is_active),
        "cooldown_hours": int(row.cooldown_hours),
        "last_fired_at": _iso_or_none(row.last_fired_at),
        "created_at": _iso_or_none(row.created_at),
    }


def _ensure_tables() -> None:
    """Best-effort init_db (mirrors forecast calibration reads)."""
    try:
        from backend.db.session import init_db

        init_db()
    except Exception:
        pass


def _get_alert_or_404(db: Session, alert_id: str) -> Alert:
    try:
        parsed = uuid.UUID(str(alert_id))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(
            status_code=404, detail=f"unknown alert {alert_id!r}"
        ) from None
    row = db.query(Alert).filter(Alert.alert_id == parsed).first()
    if row is None:  # portable fallback (SQLite stores UUIDs as CHAR(32))
        try:
            row = (
                db.query(Alert)
                .filter(cast(Alert.alert_id, SAString) == str(parsed))
                .first()
            )
        except Exception:
            row = None
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"unknown alert {alert_id!r}"
        )
    return row


# --- schemas --------------------------------------------------------------


class AlertCreate(BaseModel):
    symbol: str = Field(min_length=1, description="e.g. AAPL")
    condition: AlertCondition
    threshold: float
    horizon_days: int = Field(default=21, description="One of 5, 21, 63 (used by direction_*)")
    target_ccy: str = Field(default="USD", description="ISO 4217 code, e.g. USD")

    @field_validator("symbol")
    @classmethod
    def _strip_symbol(cls, value: str) -> str:
        text = (value or "").strip().upper()
        if not text:
            raise ValueError("symbol must be non-empty")
        return text

    @field_validator("threshold", mode="before")
    @classmethod
    def _reject_non_finite_literal(cls, value: Any) -> Any:
        # Literal NaN/Infinity tokens are not valid JSON but Python's parser
        # accepts them; pydantic would echo the raw float back in its 422
        # body and Starlette's strict serializer would then 500. Reject here
        # with an HTTPException so the client gets a clean 422 (no echo).
        if isinstance(value, float) and not math.isfinite(value):
            raise HTTPException(
                status_code=422, detail="threshold must be a finite number"
            )
        return value

    @field_validator("threshold")
    @classmethod
    def _finite_threshold(cls, value: float) -> float:
        try:
            number = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise ValueError("threshold must be a finite number") from None
        if not math.isfinite(number):
            raise ValueError("threshold must be a finite number")
        return number

    @field_validator("horizon_days")
    @classmethod
    def _check_horizon(cls, value: int) -> int:
        if int(value) not in FORECAST_HORIZONS:
            raise ValueError(
                f"horizon_days must be one of {list(FORECAST_HORIZONS)}, got {value}"
            )
        return int(value)

    @field_validator("target_ccy")
    @classmethod
    def _check_ccy(cls, value: str) -> str:
        text = ((value or "USD").strip().upper()) or "USD"
        if len(text) != 3 or not text.isalpha():
            raise ValueError(
                f"target_ccy must be a 3-letter ISO code, got {value!r}"
            )
        return text


class AlertPatch(BaseModel):
    is_active: bool | None = None
    threshold: float | None = None
    cooldown_hours: int | None = Field(default=None, ge=0)

    @field_validator("threshold", mode="before")
    @classmethod
    def _reject_non_finite_literal(cls, value: Any) -> Any:
        # Same literal-NaN guard as AlertCreate (clean 422, no echo-crash).
        if isinstance(value, float) and not math.isfinite(value):
            raise HTTPException(
                status_code=422, detail="threshold must be a finite number"
            )
        return value

    @field_validator("threshold")
    @classmethod
    def _finite_threshold(cls, value: float | None) -> float | None:
        if value is None:
            return None
        try:
            number = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise ValueError("threshold must be a finite number") from None
        if not math.isfinite(number):
            raise ValueError("threshold must be a finite number")
        return number


# --- CRUD -----------------------------------------------------------------


@router.post("", include_in_schema=False)
@router.post("/")
def create_alert(
    body: AlertCreate,
    db: Session = Depends(get_db),
    registry: InstrumentRegistry = Depends(get_registry),
) -> dict:
    """Create one alert rule (symbol must resolve via the registry)."""
    _ensure_tables()
    instrument, _, _ = registry.resolve(body.symbol)
    if instrument is None:
        raise HTTPException(
            status_code=422, detail=f"unknown symbol {body.symbol!r}"
        )
    row = Alert(
        symbol=str(instrument.exchange_symbol).upper(),
        exchange_mic=str(instrument.exchange_mic or ""),
        condition=str(body.condition),
        threshold=float(body.threshold),
        horizon_days=int(body.horizon_days),
        target_ccy=str(body.target_ccy),
        is_active=True,
        cooldown_hours=24,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "alert": _alert_to_out(row),
        "provenance": _alert_provenance(False),
        "disclosure": DISCLOSURE,
    }


@router.get("", include_in_schema=False)
@router.get("/")
def list_alerts(
    active_only: bool = Query(
        default=False, description="When true, return active alerts only"
    ),
    db: Session = Depends(get_db),
) -> dict:
    """List alert rules (creation order; never 500s on a fresh DB)."""
    _ensure_tables()
    try:
        query = db.query(Alert).order_by(Alert.created_at.asc())
        if active_only:
            query = query.filter(Alert.is_active.is_(True))
        rows = query.all()
    except Exception:
        return {
            "alerts": [], "count": 0,
            "provenance": _alert_provenance(True),
            "disclosure": DISCLOSURE,
        }
    return {
        "alerts": [_alert_to_out(r) for r in rows],
        "count": len(rows),
        "provenance": _alert_provenance(False),
        "disclosure": DISCLOSURE,
    }


@router.patch("/{alert_id}")
def update_alert(
    alert_id: str,
    body: AlertPatch,
    db: Session = Depends(get_db),
) -> dict:
    """Patch the updatable subset {is_active, threshold, cooldown_hours}."""
    if (
        body.is_active is None
        and body.threshold is None
        and body.cooldown_hours is None
    ):
        raise HTTPException(
            status_code=422,
            detail="no updatable fields: expected subset of "
            "{is_active, threshold, cooldown_hours}",
        )
    row = _get_alert_or_404(db, alert_id)
    if body.is_active is not None:
        row.is_active = bool(body.is_active)
    if body.threshold is not None:
        row.threshold = float(body.threshold)
    if body.cooldown_hours is not None:
        row.cooldown_hours = int(body.cooldown_hours)
    db.commit()
    db.refresh(row)
    return {
        "alert": _alert_to_out(row),
        "provenance": _alert_provenance(False),
        "disclosure": DISCLOSURE,
    }


@router.delete("/{alert_id}", status_code=204)
def delete_alert(alert_id: str, db: Session = Depends(get_db)) -> Response:
    """Delete one alert (fired events cascade; 204 with no body)."""
    row = _get_alert_or_404(db, alert_id)
    try:
        # Explicit event delete first: portable across Postgres (which also
        # enforces ON DELETE CASCADE) and SQLite (FK pragmas may be off).
        db.query(AlertEvent).filter(
            AlertEvent.alert_id == row.alert_id
        ).delete(synchronize_session=False)
        db.delete(row)
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        raise
    return Response(status_code=204)


# --- evaluation core (shared by cron + worker) -----------------------------


def _is_due(alert: Alert, now: datetime) -> bool:
    """True when never fired or now - last_fired_at >= cooldown_hours."""
    last = alert.last_fired_at
    if last is None:
        return True
    try:
        cooldown = float(alert.cooldown_hours or 0)
    except (TypeError, ValueError):
        cooldown = 24.0
    if cooldown <= 0:
        return True
    if isinstance(last, str):
        try:
            last = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return True
    if isinstance(last, datetime) and last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    try:
        return (now - last).total_seconds() >= cooldown * 3600.0  # type: ignore[operator]
    except (TypeError, OverflowError):
        return True


def _condition_met(condition: str, observed: float, threshold: float) -> bool:
    """Above-conditions fire on observed >= threshold; below on <=."""
    if str(condition).endswith("_above"):
        return float(observed) >= float(threshold)
    return float(observed) <= float(threshold)


def _finite_or_raise(value: Any, what: str, symbol: str) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError(f"no {what} for {symbol!r}") from None
    if not math.isfinite(number):
        raise ValueError(f"no {what} for {symbol!r}")
    return number


def _observe(
    alert: Alert, *, market: Any, forecast: Any
) -> tuple[float, dict]:
    """Fetch the observed value + observation provenance for one alert."""
    condition = str(alert.condition)
    symbol = str(alert.symbol)
    if condition in ("price_above", "price_below"):
        quote = market.get_quote(symbol)
        observed = _finite_or_raise(quote.get("price"), "price", symbol)
        return observed, dict(quote.get("provenance") or {})
    if condition == "change_pct_below":
        quote = market.get_quote(symbol)
        observed = _finite_or_raise(quote.get("change_pct"), "change_pct", symbol)
        return observed, dict(quote.get("provenance") or {})
    if condition in DIRECTION_CONDITIONS:
        result = forecast.forecast(symbol, int(alert.horizon_days))
        observed = _finite_or_raise(
            result.get("direction_probability"),
            "direction_probability",
            symbol,
        )
        return observed, dict(result.get("provenance") or {})
    raise ValueError(f"unknown condition {condition!r}")


def evaluate_due_alerts(
    db: Session,
    *,
    market: Any = None,
    forecast: Any = None,
    notifier: Any = None,
    now: datetime | None = None,
) -> dict:
    """Evaluate every active due alert; shared core (cron + worker).

    Returns ``{checked, fired, errors, provenance, disclosure}`` where
    ``checked`` counts due alerts successfully observed (met or not),
    ``fired`` lists ``{alert_id, symbol, observed}``, and ``errors`` maps
    alert_id (or ``_batch``) to a short reason. Per-alert failures —
    including insufficient history — land in ``errors``; the batch itself
    never raises for them.
    """
    if market is None:
        from backend.market_data.service import MarketDataService

        market = MarketDataService()
    if forecast is None:
        from backend.forecasting.service import ForecastService

        forecast = ForecastService()
    if notifier is None:
        from backend.api.alerts_notify import get_notifier

        notifier = get_notifier()
    moment = now or _utcnow()

    try:
        alerts = (
            db.query(Alert)
            .filter(Alert.is_active.is_(True))
            .order_by(Alert.created_at.asc())
            .all()
        )
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return {
            "checked": 0,
            "fired": [],
            "errors": {"_batch": "db unavailable"},
            "provenance": _alert_provenance(True),
            "disclosure": DISCLOSURE,
        }

    checked = 0
    fired: list[dict] = []
    errors: dict[str, str] = {}
    for alert in alerts:
        aid = str(alert.alert_id)
        if not _is_due(alert, moment):
            continue
        try:
            observed, obs_provenance = _observe(
                alert, market=market, forecast=forecast
            )
        except ValueError as exc:
            # Includes the insufficient-history skip (reason preserved).
            errors[aid] = str(exc)[:200] or type(exc).__name__
            continue
        except Exception as exc:
            errors[aid] = f"{type(exc).__name__}: {str(exc)[:200]}"
            continue
        checked += 1
        try:
            met = _condition_met(
                str(alert.condition), observed, float(alert.threshold)
            )
        except Exception as exc:
            errors[aid] = f"{type(exc).__name__}: {str(exc)[:200]}"
            continue
        if not met:
            continue
        # Fire: event row + last_fired_at commit atomically; audit and
        # delivery are best-effort afterwards and never block the firing.
        event_provenance = dict(obs_provenance or {})
        event_provenance.setdefault("condition", str(alert.condition))
        try:
            db.add(AlertEvent(
                alert_id=alert.alert_id,
                symbol=str(alert.symbol),
                observed=float(observed),
                threshold=float(alert.threshold),
                provenance=event_provenance,
            ))
            alert.last_fired_at = moment
            db.commit()
        except Exception as exc:
            try:
                db.rollback()
            except Exception:
                pass
            errors[aid] = f"{type(exc).__name__}: {str(exc)[:200]}"
            continue
        try:
            from backend.api.audit import append_audit_log

            append_audit_log(
                db,
                actor="system",
                action="alert.fired",
                entity_type="alert",
                entity_id=aid,
                payload={
                    "symbol": str(alert.symbol),
                    "condition": str(alert.condition),
                    "observed": float(observed),
                    "threshold": float(alert.threshold),
                    "horizon_days": int(alert.horizon_days),
                },
            )
        except Exception:
            logger.warning("alert audit append failed")
        try:
            notifier.notify({
                "alert_id": aid,
                "symbol": str(alert.symbol),
                "condition": str(alert.condition),
                "observed": float(observed),
                "threshold": float(alert.threshold),
                "horizon_days": int(alert.horizon_days),
                "provenance": obs_provenance,
            })
        except Exception:
            logger.warning("alert delivery failed")
        fired.append({
            "alert_id": aid,
            "symbol": str(alert.symbol),
            "observed": float(observed),
        })
    return {
        "checked": checked,
        "fired": fired,
        "errors": errors,
        "provenance": _alert_provenance(bool(errors)),
        "disclosure": DISCLOSURE,
    }


__all__ = [
    "DISCLOSURE",
    "ALERT_CONDITIONS",
    "DIRECTION_CONDITIONS",
    "router",
    "evaluate_due_alerts",
]
