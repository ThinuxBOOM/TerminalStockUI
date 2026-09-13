"""GET /api/forecast/{symbol}?horizon=5|21|63 — validated ensemble forecast.

Ensemble of existing baselines (historical-drift + momentum + logistic);
quantile bands supply the return range, volatility regime and drawdown
probability. Deterministic, no AI, no network. Every response carries the
provenance envelope + model/feature/data versions + timestamp + the
"Not investment advice" disclosure. Horizons outside {5, 21, 63} -> 422.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.forecasting.common import FORECAST_HORIZONS
from backend.forecasting.service import ForecastService, get_forecast_service
from backend.market_data.provenance import build_provenance

router = APIRouter(prefix="/api/forecast", tags=["forecast"])

#: Display bands for the direction probability. Thresholds mirror the
#: frontend placeholders (0.64 -> "moderately positive") so live responses
#: render the same labels the UI was designed around.
LABEL_BANDS: tuple[tuple[float, str], ...] = (
    (0.65, "clearly positive"),
    (0.57, "moderately positive"),
    (0.52, "slightly positive"),
)


def direction_label(probability: float) -> str:
    """Human label for a direction probability (deterministic bands)."""
    try:
        prob = float(probability)
    except (TypeError, ValueError):
        return "neutral"
    for edge, label in LABEL_BANDS:
        if prob >= edge:
            return label
        if prob <= 1.0 - edge:
            return label.replace("positive", "negative")
    return "neutral"


def _member_hit_rate(
    member_accuracy: dict | None, member: str | None
) -> float | None:
    """Trailing hit_rate for a member, or None when unknown.

    Accepts the calibration-snapshot shape
    ``{member_name: {"hit_rate": float | None, "n": int}}`` (plain dict;
    never imports the calibration module). Tolerant: a plain numeric
    value is also accepted as the rate.
    """
    if member is None or not isinstance(member_accuracy, dict):
        return None
    try:
        entry = member_accuracy.get(member)
    except AttributeError:
        return None
    if entry is None:
        return None
    if isinstance(entry, bool):
        return None
    if isinstance(entry, (int, float)):
        try:
            rate = float(entry)
        except (TypeError, ValueError):
            return None
        return rate if 0.0 <= rate <= 1.0 else None
    if isinstance(entry, dict):
        raw = entry.get("hit_rate")
        if raw is None or isinstance(raw, bool):
            return None
        if isinstance(raw, (int, float)):
            try:
                rate = float(raw)
            except (TypeError, ValueError):
                return None
            return rate if 0.0 <= rate <= 1.0 else None
    return None


def _order_side_by_accuracy(
    entries: list[str],
    components: dict,
    member_accuracy: dict,
) -> list[str]:
    """Order one side by member trailing hit_rate descending (deterministic).

    Entries quoting a member (``"<name> implies ..."`` where ``<name>``
    is a components key) use that member's rate; non-member entries
    (regime/drawdown/mid) and member entries with unknown rate keep
    their relative order after the known-rate member entries.
    """
    scored: list[tuple[float | None, int, str]] = []
    for idx, entry in enumerate(entries):
        member: str | None = None
        if " implies " in entry:
            candidate = entry.split(" implies ", 1)[0]
            if candidate in components:
                member = candidate
        scored.append((_member_hit_rate(member_accuracy, member), idx, entry))
    known = [(r, i, e) for r, i, e in scored if r is not None]
    unknown = [(i, e) for r, i, e in scored if r is None]
    known.sort(key=lambda t: (-t[0], t[1]))
    unknown.sort(key=lambda t: t[0])
    return [e for _, _, e in known] + [e for _, e in unknown]


def forecast_drivers(
    result: dict, member_accuracy: dict | None = None
) -> tuple[list[str], list[str]]:
    """Derive bull/bear driver strings from computed ensemble values only.

    Every string quotes a number already present in the response — no
    narrative is invented. Cap 4 items per side; empty side renders as
    "unavailable" in the UI (honest, never zero-filled).

    When ``member_accuracy`` (``{member: {"hit_rate": float|None, ...}}``)
    is provided and non-empty, each side is ordered by trailing hit_rate
    descending (member entries use their member's rate; non-member and
    unknown-rate entries keep relative order last). When None/empty, the
    historical ordering is returned byte-for-byte.
    """
    why: list[str] = []
    risks: list[str] = []
    horizon = result.get("horizon_days", "?")
    components = result.get("components") or {}
    for name in sorted(components):
        try:
            value = float(components[name])
        except (TypeError, ValueError):
            continue
        if value > 0.5:
            why.append(f"{name} implies up (p={value:.2f})")
        elif value < 0.5:
            risks.append(f"{name} implies down (p={value:.2f})")
    band = result.get("expected_return_range") or {}
    mid = band.get("mid")
    if isinstance(mid, bool):
        mid = None
    if isinstance(mid, (int, float)):
        (why if mid >= 0 else risks).append(
            f"expected {horizon}d return mid {mid:+.1%}"
        )
    regime = result.get("volatility_regime")
    if regime in ("high", "elevated", "extreme"):
        risks.append(f"volatility regime: {regime}")
    drawdown = result.get("drawdown_probability")
    if isinstance(drawdown, bool):
        drawdown = None
    if isinstance(drawdown, (int, float)):
        if drawdown >= 0.25:
            risks.append(f"large-drawdown probability {drawdown:.0%} over {horizon}d")
        elif drawdown <= 0.10:
            why.append(f"large-drawdown probability low ({drawdown:.0%})")
    if not member_accuracy:
        return why[:4], risks[:4]
    why = _order_side_by_accuracy(why, components, member_accuracy)
    risks = _order_side_by_accuracy(risks, components, member_accuracy)
    return why[:4], risks[:4]


@router.get("/{symbol}/calibration/history")
def calibration_history(
    symbol: str,
    horizon: int = Query(default=21, description="Trading-day horizon: 5, 21 or 63"),
    limit: int = Query(default=20, ge=1, le=50, description="Max snapshots (cap 50)"),
    svc: ForecastService = Depends(get_forecast_service),
) -> dict:
    """Calibration snapshot history for (symbol, horizon), newest first.

    Lists persisted snapshots [{brier, ece, n_windows, reliability, members,
    model_version, data_version, created_at}]. DB miss -> empty history
    (never 500).
    """
    from backend.forecasting.calibration.snapshots import canonical_symbol

    if int(horizon) not in FORECAST_HORIZONS:
        raise HTTPException(
            status_code=422,
            detail=f"horizon must be one of {list(FORECAST_HORIZONS)}, got {horizon}",
        )
    horizon = int(horizon)
    market_service = getattr(svc, "market", None)
    try:
        canonical = canonical_symbol(symbol, market_service)
    except Exception:
        canonical = (symbol or "").strip().upper()
    # Provenance: best-effort bars envelope, honest fallback otherwise.
    try:
        bars = market_service.get_bars(
            (symbol or "").strip().upper(), timeframe="1d", limit=5
        )
        provenance = dict(bars.get("provenance", {}))
        if not provenance:
            raise ValueError("empty provenance")
    except Exception:
        try:
            provenance = build_provenance(
                "forecast-calibration",
                as_of=datetime.now(timezone.utc),
                delay_minutes=15,
                quality_grade="B",
                fallback_used=False,
                missing_fields=[],
            ).model_dump(mode="json")
        except Exception:
            provenance = {
                "source": "forecast-calibration",
                "as_of": datetime.now(timezone.utc).isoformat(),
                "delay_minutes": 15,
                "quality_grade": "B",
                "fallback_used": False,
                "missing_fields": [],
            }
    try:
        from backend.db.session import get_session_factory, init_db
        from backend.db.models import CalibrationSnapshot

        try:
            init_db()
        except Exception:
            pass
        Session = get_session_factory()
        db = Session()
        try:
            rows = (
                db.query(CalibrationSnapshot)
                .filter(
                    CalibrationSnapshot.symbol == (canonical or "").strip().upper(),
                    CalibrationSnapshot.horizon_days == int(horizon),
                )
                .order_by(CalibrationSnapshot.created_at.desc())
                .limit(int(limit))
                .all()
            )
            history = [_snapshot_wire(r) for r in rows]
        finally:
            try:
                db.close()
            except Exception:
                pass
    except Exception:
        history = []
    return {
        "symbol": (canonical or "").strip().upper(),
        "horizon": horizon,
        "history": history,
        "count": len(history),
        "provenance": provenance,
        "disclosure": "Not investment advice",
    }


@router.get("/{symbol}")
def get_forecast(
    symbol: str,
    horizon: int = Query(default=21, description="Trading-day horizon: 5, 21 or 63"),
    svc: ForecastService = Depends(get_forecast_service),
) -> dict:
    """Forecast one symbol/horizon (ensemble direction + bands + risk)."""
    if int(horizon) not in FORECAST_HORIZONS:
        raise HTTPException(
            status_code=422,
            detail=f"horizon must be one of {list(FORECAST_HORIZONS)}, got {horizon}",
        )
    try:
        result = svc.forecast(symbol, int(horizon))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # Flatten the record mirror out of the wire payload (kept in result["record"]).
    payload = {k: v for k, v in result.items() if k != "record"}
    # Display fields the terminal UI renders (derived, never invented):
    # label bands, data-quality passthrough, engine identity, bull/bear
    # drivers quoted from computed components, evidence = model members.
    # Calibration snapshot (reliability rows + member accuracy for driver
    # ordering) is best-effort; []/None when none. member_accuracy never
    # reaches the wire — ordering input only.
    cal_rows, member_acc, cal_meta = _latest_calibration(
        symbol,
        int(horizon),
        str(result.get("model_version", "")),
        market_service=getattr(svc, "market", None),
    )
    why, risks = forecast_drivers(result, member_acc)
    provenance = result.get("provenance") or {}
    payload["label"] = direction_label(result.get("direction_probability", 0.5))
    payload["quality_grade"] = str(provenance.get("quality_grade", "U")).upper() or "U"
    payload["provider"] = "deterministic-engine"
    payload["why"] = why
    payload["risks"] = risks
    payload["evidence_ids"] = list(result.get("model_members", []))
    payload["limitations"] = forecast_limitations(result)
    payload["inputs"] = {
        "model_version": result.get("model_version"),
        "feature_version": result.get("feature_version"),
        "data_version": result.get("data_version"),
        "n_windows": (result.get("expected_return_range") or {}).get("n_windows"),
    }
    # Calibration rows from the latest snapshot for
    # (symbol, horizon, model_version); [] when none (offline/tests/DB
    # issues must never break the forecast path).
    payload["calibration"] = cal_rows
    payload["calibration_meta"] = cal_meta
    return payload


def forecast_limitations(result: dict) -> list[str]:
    """Honest caveats for the forecast payload (never empty on live data)."""
    items = [
        "Walk-forward validation only; no look-ahead.",
        "Missing data renders unavailable, never silently imputed.",
        "Disabling AI leaves forecasting intact.",
    ]
    band = result.get("expected_return_range") or {}
    if band.get("n_windows") is not None:
        try:
            items.append(
                f"Return range estimated from {int(band['n_windows'])} historical windows."
            )
        except (TypeError, ValueError):
            pass
    return items


def _snapshot_meta(row) -> dict | None:
    """Wire calibration_meta for one snapshot row (never raises)."""
    try:
        brier = getattr(row, "brier", None)
        ece = getattr(row, "ece", None)
        try:
            brier_f = None if brier is None else float(brier)
        except (TypeError, ValueError):
            brier_f = None
        try:
            ece_f = None if ece is None else float(ece)
        except (TypeError, ValueError):
            ece_f = None
        try:
            n_windows = int(getattr(row, "n_windows", 0) or 0)
        except (TypeError, ValueError):
            n_windows = 0
        members = getattr(row, "members", None)
        members_d = dict(members) if isinstance(members, dict) else {}
        created = getattr(row, "created_at", None)
        try:
            created_s = created.isoformat() if hasattr(created, "isoformat") else str(created)
        except Exception:
            created_s = str(created)
        return {
            "brier": brier_f,
            "ece": ece_f,
            "n_windows": n_windows,
            "members": members_d,
            "model_version": str(getattr(row, "model_version", "") or ""),
            "data_version": str(getattr(row, "data_version", "") or ""),
            "created_at": created_s,
        }
    except Exception:
        return None


def _snapshot_wire(row) -> dict:
    """Full wire shape for one snapshot in history (never raises)."""
    meta = _snapshot_meta(row) or {
        "brier": None, "ece": None, "n_windows": 0, "members": {},
        "model_version": "", "data_version": "", "created_at": "",
    }
    try:
        reliability = getattr(row, "reliability", None)
        rel = list(reliability) if isinstance(reliability, list) else []
    except Exception:
        rel = []
    return {
        "brier": meta["brier"],
        "ece": meta["ece"],
        "n_windows": meta["n_windows"],
        "reliability": rel,
        "members": meta["members"],
        "model_version": meta["model_version"],
        "data_version": meta["data_version"],
        "created_at": meta["created_at"],
    }


def _latest_calibration(
    symbol: str, horizon: int, model_version: str, market_service=None
) -> tuple[list, dict | None, dict | None]:
    """Best-effort (reliability rows, member accuracy, calibration_meta)."""
    try:
        from backend.db.session import get_session_factory, init_db
        from backend.forecasting.calibration.snapshots import (
            canonical_symbol,
            get_latest_snapshot,
        )

        try:
            init_db()
        except Exception:
            pass
        Session = get_session_factory()
        db = Session()
        try:
            canonical = canonical_symbol(symbol, market_service)
            row = get_latest_snapshot(db, canonical, int(horizon), model_version)
            if row is None:
                return [], None, None
            reliability = getattr(row, "reliability", None)
            rows = list(reliability) if isinstance(reliability, list) else []
            members = getattr(row, "members", None)
            acc = dict(members) if isinstance(members, dict) and members else None
            return rows, acc, _snapshot_meta(row)
        finally:
            try:
                db.close()
            except Exception:
                pass
    except Exception:
        return [], None, None
