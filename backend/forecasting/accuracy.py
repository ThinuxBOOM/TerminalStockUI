"""Prediction-accuracy tracking + confidence evolution (point-in-time).

Scoring formula (per forecast, computed only once its horizon is observable)::

    realized_return = target_close / base_close - 1   (both closes are stored
                        bars observed at/before their own dates — never
                        live/future data)
    realized_label  = 1 if realized_return > 0 else 0
    hit             = (realized_label == 1) == (direction_prob >= 0.5)
    brier_contrib   = (direction_prob - realized_label) ** 2

Point-in-time: ``base_close`` is the last stored bar at/before the forecast
``created_at``; ``target_close`` the last stored bar at/before ``target_date``.
A missing/insufficient bar on either side leaves the forecast *unscored*
(never imputed), so replayed scores match what was knowable at the time.

Survivorship-aware: scoring iterates over *all* forecast rows including
instruments with ``is_active=False`` (delisted), and per-forecast accuracy
rows follow the retention ``forecast_accuracy`` rule (tied to the forecasts
window). The *aggregate* signal lives forever: every scored batch merges its
trailing window into ``calibration_snapshots.members["realized"]``, and no
retention rule purges calibration snapshots — losers are never dropped from
the long-run denominator.

Confidence evolution: every scored row carries ``confidence_before`` (the
label the forecast shipped with) and ``confidence_after`` derived from the
trailing realized window for its (symbol, horizon) via
:func:`trailing_stats`::

    n < 10                              -> "low"    (insufficient evidence)
    hit_rate >= 0.65 and brier <= 0.20  -> "high"
    hit_rate >= 0.55                    -> "moderate"
    else                                -> "low"

Calibration linkage: :func:`score_due_forecasts` merges the realized window
into the latest ``calibration_snapshots`` row's ``members["realized"]`` entry
(``{n, hit_rate, brier_mean}``) without touching the walk-forward
``brier``/``ece`` values, so the dashboard keeps both replay skill and live
outcomes.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Callable

logger = logging.getLogger(__name__)

SCORING_FORMULA = (
    "realized_return = target_close/base_close - 1; "
    "y = 1 if realized_return > 0 else 0; "
    "hit = (y == 1) == (p >= 0.5); brier = (p - y)^2"
)

#: Minimum realized outcomes before a non-low suggestion is allowed.
MIN_EVOLUTION_WINDOWS = 10
#: Trailing hit-rate for a "high" suggestion (with the Brier gate below).
HIGH_MIN_HIT_RATE = 0.65
#: Trailing mean Brier contribution ceiling for a "high" suggestion.
HIGH_MAX_BRIER = 0.20
#: Trailing hit-rate floor for a "moderate" suggestion.
MODERATE_MIN_HIT_RATE = 0.55


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _finite_prob(value: Any) -> float:
    try:
        prob = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"direction_prob must be numeric, got {value!r}") from exc
    if prob != prob or prob in (float("inf"), float("-inf")):
        raise ValueError(f"direction_prob must be finite, got {value!r}")
    if not 0.0 <= prob <= 1.0:
        raise ValueError(f"direction_prob must be in [0, 1], got {prob!r}")
    return prob


def _positive_close(value: Any, name: str) -> float:
    try:
        close = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric, got {value!r}") from exc
    if close != close or close in (float("inf"), float("-inf")) or close <= 0:
        raise ValueError(f"{name} must be a positive finite price, got {value!r}")
    return close


def score_one(predicted_prob: float, base_close: float,
              target_close: float) -> dict:
    """Score one forecast outcome (pure; raises ``ValueError`` on bad input)."""
    prob = _finite_prob(predicted_prob)
    base = _positive_close(base_close, "base_close")
    target = _positive_close(target_close, "target_close")
    realized_return = target / base - 1.0
    realized_label = 1 if realized_return > 0 else 0
    hit = bool((realized_label == 1) == (prob >= 0.5))
    return {
        "realized_return": float(realized_return),
        "realized_label": int(realized_label),
        "hit": hit,
        "brier_contrib": float((prob - realized_label) ** 2),
    }


def trailing_stats(hits: list[bool],
                   briers: list[float]) -> dict:
    """Trailing realized window -> ``{n, hit_rate, brier_mean, suggested}``.

    Empty windows yield ``hit_rate=None``/``brier_mean=None`` with a "low"
    suggestion (no evidence must never read as skill). Non-finite Brier
    entries are ignored; a window of only non-finite entries also yields
    None metrics.
    """
    n = len(hits)
    try:
        clean = [float(b) for b in (briers or [])[:n]
                 if isinstance(b, (int, float)) and b == b
                 and b not in (float("inf"), float("-inf"))]
    except Exception:
        clean = []
    if n == 0:
        return {"n": 0, "hit_rate": None, "brier_mean": None, "suggested": "low"}
    hit_rate = float(sum(1 for h in hits if h) / n)
    brier_mean = float(sum(clean) / len(clean)) if clean else None
    if n < MIN_EVOLUTION_WINDOWS:
        suggested = "low"
    elif hit_rate >= HIGH_MIN_HIT_RATE and brier_mean is not None and brier_mean <= HIGH_MAX_BRIER:
        suggested = "high"
    elif hit_rate >= MODERATE_MIN_HIT_RATE:
        suggested = "moderate"
    else:
        suggested = "low"
    return {"n": n, "hit_rate": hit_rate, "brier_mean": brier_mean,
            "suggested": suggested}


def confidence_trajectory(scored: list[dict]) -> list[dict]:
    """Cumulative confidence evolution over time-ordered scored rows.

    Each input row needs ``{scored_at, hit, brier_contrib}``; output rows add
    the cumulative ``{n, hit_rate, brier_mean, suggested}`` after that event.
    Pure helper for dashboards/tests; never raises on empty input.
    """
    ordered = sorted(scored, key=lambda r: str(r.get("scored_at") or ""))
    hits: list[bool] = []
    briers: list[float] = []
    out: list[dict] = []
    for row in ordered:
        try:
            hits.append(bool(row.get("hit")))
            briers.append(float(row.get("brier_contrib")))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        stats = trailing_stats(list(hits), list(briers))
        out.append({**dict(row), **stats})
    return out


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def score_due_forecasts(db: Any, *,
                        symbols: list[str] | None = None,
                        now: datetime | None = None,
                        price_lookup: Callable[[str, date], float | None] | None = None,
                        limit: int = 500) -> dict:
    """Score matured forecasts point-in-time; persist accuracy + calibration.

    A forecast is *due* when ``target_date <= today`` and no accuracy row
    exists for its ``forecast_id``. Closes resolve via ``price_lookup`` when
    given, else from stored ``price_bars`` (last bar at/before each date —
    point-in-time, survivorship-aware: inactive instruments stay eligible).
    Missing closes leave the forecast unscored (counted, never imputed).

    Returns ``{scored, unscored, errors, hits, brier_mean}``. Graceful when
    the forecasts/accuracy tables are missing (all zeros + ``reason``).
    Never raises.
    """
    moment = now or _utcnow()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    today = moment.date()
    wanted = {(s or "").strip().upper() for s in (symbols or []) if (s or "").strip()}

    def _lookup(instrument_id: Any, day: date | None) -> float | None:
        if day is None:
            return None
        if price_lookup is not None:
            try:
                return price_lookup(str(instrument_id), day)
            except Exception:
                return None
        try:
            from backend.db.models import PriceBar

            bars = (
                db.query(PriceBar)
                .filter(PriceBar.instrument_id == instrument_id,
                        PriceBar.timeframe == "1d")
                .order_by(PriceBar.ts.desc())
                .limit(400)
                .all()
            )
            for bar in bars:
                ts = getattr(bar, "ts", None)
                day_ts = ts.date() if isinstance(ts, datetime) else _as_date(ts)
                if day_ts is not None and day_ts <= day and bar.close is not None:
                    try:
                        close = float(bar.close)
                    except (TypeError, ValueError):
                        continue
                    if close == close and close not in (float("inf"), float("-inf")) and close > 0:
                        return close
            return None
        except Exception:
            return None

    try:
        from backend.db.models import Forecast as DBForecast

        query = db.query(DBForecast).order_by(DBForecast.created_at.asc()).limit(max(1, limit))
        forecasts = list(query.all())
    except Exception as exc:
        return {"scored": 0, "unscored": 0, "errors": {},
                "hits": 0, "brier_mean": None,
                "reason": f"{type(exc).__name__}: forecasts table missing or unreadable"}

    try:
        from backend.db.models import ForecastAccuracy as DBAccuracy

        have_accuracy_table = True
    except Exception:
        have_accuracy_table = False
        DBAccuracy = None  # type: ignore[assignment]

    scored = unscored = hits = 0
    briers: list[float] = []
    errors: dict[str, str] = {}
    per_key: dict[tuple[str, int], list[dict]] = {}
    for fc in forecasts:
        try:
            horizon = int(fc.horizon_days)
            target = _as_date(fc.target_date)
            created = fc.created_at
            if isinstance(created, str):
                try:
                    created = datetime.fromisoformat(created)
                except ValueError:
                    created = None
            base_day = created.date() if isinstance(created, datetime) else None
        except Exception as exc:
            errors[str(getattr(fc, "forecast_id", "?"))] = type(exc).__name__
            continue
        if target is None or target > today:
            continue  # horizon not yet observable: skip, never impute
        fid = str(getattr(fc, "forecast_id", ""))
        try:
            if have_accuracy_table and fid:
                exists = (db.query(DBAccuracy)
                          .filter(DBAccuracy.forecast_id == fc.forecast_id)
                          .first())
                if exists is not None:
                    continue  # already scored: idempotent re-runs skip
        except Exception:
            pass
        try:
            prob_raw = fc.direction_prob
            prob = None if prob_raw is None else float(prob_raw)
        except (TypeError, ValueError):
            prob = None
        if prob is None:
            unscored += 1
            continue
        base_close = _lookup(fc.instrument_id, base_day)
        target_close = _lookup(fc.instrument_id, target)
        if base_close is None or target_close is None:
            unscored += 1
            continue
        try:
            outcome = score_one(prob, base_close, target_close)
        except ValueError as exc:
            errors[fid or "?"] = f"{type(exc).__name__}: {exc}"
            unscored += 1
            continue
        # Resolve (symbol, mic) for the accuracy row + calibration linkage.
        # Survivorship-aware: inactive instruments resolve the same way.
        symbol, mic = str(fc.instrument_id), ""
        try:
            from backend.db.models import Instrument as DBInstrument

            inst = (db.query(DBInstrument)
                    .filter(DBInstrument.instrument_id == fc.instrument_id)
                    .first())
            if inst is not None:
                symbol = str(inst.exchange_symbol or symbol).upper()
                mic = str(inst.exchange_mic or "").upper()
        except Exception:
            pass
        if wanted and symbol.upper() not in wanted:
            continue
        key = (symbol.upper(), horizon)
        per_key.setdefault(key, []).append(outcome)
        stats = trailing_stats([o["hit"] for o in per_key[key]],
                               [o["brier_contrib"] for o in per_key[key]])
        confidence_after = stats["suggested"]
        if have_accuracy_table:
            try:
                db.add(DBAccuracy(
                    forecast_id=fc.forecast_id,
                    symbol=symbol.upper(),
                    exchange_mic=mic or "XNAS",
                    horizon_days=horizon,
                    target_date=target,
                    predicted_prob=prob,
                    realized_label=outcome["realized_label"],
                    realized_return=outcome["realized_return"],
                    # Union mirror for revamp readers (NUMERIC(10,6)).
                    realized_ret=round(outcome["realized_return"], 6),
                    hit=outcome["hit"],
                    brier_contrib=outcome["brier_contrib"],
                    confidence_before=(fc.confidence or None),
                    confidence_after=confidence_after,
                    model_version=str(fc.model_version or ""),
                    data_version=str(fc.data_version or ""),
                    user_id=None,  # nullable hook for future per-user tracking
                    scored_at=moment,
                ))
                db.commit()
            except Exception as exc:
                try:
                    db.rollback()
                except Exception:
                    pass
                errors[fid or "?"] = f"{type(exc).__name__}: accuracy write failed"
                unscored += 1
                continue
        scored += 1
        hits += 1 if outcome["hit"] else 0
        briers.append(outcome["brier_contrib"])
        _refresh_calibration_member(db, symbol.upper(), mic or "XNAS", horizon,
                                    str(fc.model_version or ""), per_key[key])

    return {
        "scored": scored,
        "unscored": unscored,
        "errors": errors,
        "hits": hits,
        "brier_mean": (float(sum(briers) / len(briers)) if briers else None),
    }


def _refresh_calibration_member(db: Any, symbol: str, mic: str, horizon: int,
                                model_version: str, outcomes: list[dict]) -> None:
    """Merge the realized window into the latest calibration row (additive).

    Writes ``members["realized"] = {n, hit_rate, brier_mean}``; the
    walk-forward ``brier``/``ece``/``reliability`` values are never touched.
    Best-effort: missing table/row is a silent skip (never raises).
    """
    try:
        from backend.db.models import CalibrationSnapshot

        row = (
            db.query(CalibrationSnapshot)
            .filter(CalibrationSnapshot.symbol == symbol,
                    CalibrationSnapshot.horizon_days == int(horizon),
                    CalibrationSnapshot.model_version == str(model_version))
            .order_by(CalibrationSnapshot.created_at.desc())
            .first()
        )
        if row is None:
            return
        stats = trailing_stats([bool(o.get("hit")) for o in outcomes],
                               [float(o.get("brier_contrib", 0.0)) for o in outcomes])
        members = dict(row.members or {})
        members["realized"] = {"n": stats["n"], "hit_rate": stats["hit_rate"],
                               "brier_mean": stats["brier_mean"]}
        row.members = members
        try:
            # SQLAlchemy JSON mutation needs an explicit flag on some backends.
            from sqlalchemy.orm.attributes import flag_modified

            flag_modified(row, "members")
        except Exception:
            pass
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass


__all__ = [
    "HIGH_MAX_BRIER",
    "HIGH_MIN_HIT_RATE",
    "MIN_EVOLUTION_WINDOWS",
    "MODERATE_MIN_HIT_RATE",
    "SCORING_FORMULA",
    "confidence_trajectory",
    "score_due_forecasts",
    "score_one",
    "trailing_stats",
]
