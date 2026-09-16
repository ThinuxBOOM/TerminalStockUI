"""GET /api/forecast/{symbol}?horizon=5|21|63 — validated ensemble forecast.

Ensemble of existing baselines (historical-drift + momentum + logistic);
quantile bands supply the return range, volatility regime and drawdown
probability. Deterministic, no AI, no network. Every response carries the
provenance envelope + model/feature/data versions + timestamp + the
"Not investment advice" disclosure. Horizons outside {5, 21, 63} -> 422.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from backend.forecasting.common import FORECAST_HORIZONS
from backend.forecasting.service import ForecastService, get_forecast_service
from backend.market_data.provenance import build_provenance
from backend.security.validation import sanitize_error, validate_symbol

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

    ensemble-v2: member entries now include weights
    (``"<name> implies up (p=0.62, w=0.25)"``); spread/disagreement and
    calibration notes live in limitations (not drivers) to preserve the
    4-per-side contract.

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
    weights = result.get("ensemble_weights") or {}
    for name in sorted(components):
        try:
            value = float(components[name])
        except (TypeError, ValueError):
            continue
        try:
            w = weights.get(name)
            w_txt = f", w={float(w):.2f}" if isinstance(w, (int, float)) and not isinstance(w, bool) else ""
        except Exception:
            w_txt = ""
        if value > 0.5:
            why.append(f"{name} implies up (p={value:.2f}{w_txt})")
        elif value < 0.5:
            risks.append(f"{name} implies down (p={value:.2f}{w_txt})")
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
    # Provenance: live bars envelope. Fail-closed: when bars are
    # unavailable there is nothing honest to contextualize the history
    # with, so the request raises instead of fabricating an envelope.
    try:
        bars = market_service.get_bars(
            (symbol or "").strip().upper(), timeframe="1d", limit=5
        )
        provenance = dict(bars.get("provenance", {}))
        if not provenance:
            raise ValueError("empty provenance")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=sanitize_error(exc, prefix="calibration history failed")
        ) from exc
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
    background_tasks: BackgroundTasks,
    horizon: int = Query(default=21, description="Trading-day horizon: 5, 21 or 63"),
    svc: ForecastService = Depends(get_forecast_service),
) -> dict:
    """Forecast one symbol/horizon (ensemble direction + bands + risk)."""
    if int(horizon) not in FORECAST_HORIZONS:
        raise HTTPException(
            status_code=422,
            detail=f"horizon must be one of {list(FORECAST_HORIZONS)}, got {horizon}",
        )
    symbol = validate_symbol(symbol)
    try:
        result = svc.forecast(symbol, int(horizon))
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        try:
            from backend.market_data.providers.base import ProviderError as _PE

            if isinstance(exc, _PE):
                raise HTTPException(status_code=502, detail=sanitize_error(exc)) from exc
        except HTTPException:
            raise
        except Exception:
            pass
        try:
            raise HTTPException(status_code=502, detail=sanitize_error(exc, prefix="forecast failed")) from exc
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=502, detail="forecast failed") from exc
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
    # Persist the versioned record so GET /api/audit/forecasts (homepage
    # "Latest research") is fed by live runs. Off the read path: scheduled
    # as a background task so the forecast response never waits on the DB
    # write (find-or-create instrument + forecast + audit). Best-effort:
    # DB miss / constraint / offline -> skip, never breaks the path.
    try:
        market_service = getattr(svc, "market", None)
        try:
            background_tasks.add_task(
                _persist_forecast_record, result, symbol,
                market_service=market_service,
            )
        except Exception:
            _persist_forecast_record(result, symbol, market_service=market_service)
    except Exception:
        pass
    return payload


def forecast_limitations(result: dict) -> list[str]:
    """Honest caveats for the forecast payload (never empty on live data)."""
    items = [
        "Walk-forward validation only; no look-ahead.",
        "Missing data renders unavailable, never silently imputed.",
        "Disabling AI leaves forecasting intact.",
        "Direction probabilities are shrinkage-calibrated weighted means "
        "(shrinkage 0.8 toward 0.5, clipped to [0.05, 0.95]; raw mean in "
        "direction_probability_raw) — not isotonic-calibrated; see ECE. "
        "Fixed reliability weights (ML 0.25 each, drift/momentum 0.20 each, "
        "trend 0.10), renormalized over members that ran; not per-symbol "
        "adaptive (V2.1 hook). "
        "Confidence labels reflect ensemble agreement downgraded by "
        "data-quality and trailing-risk signals (vol regime, drawdown, "
        "staleness), not calibrated skill. High agreement near 0.5 caps at "
        "moderate; AI disagreement downgrades the blend label.",
        "ensemble-v2 members: historical-drift + momentum + logistic-v3 "
        "(v2 features) + gradient-boost-v1 (v2 features) + trend-persistence; "
        "venue blends add sse/eux drift 50/50 then recalibrate.",
    ]
    band = result.get("expected_return_range") or {}
    if band.get("n_windows") is not None:
        try:
            n = int(band['n_windows'])
            ne = band.get("n_effective")
            ne_txt = f" (~{float(ne):.1f} independent blocks)" if isinstance(ne, (int, float)) else ""
            items.append(
                f"Return range estimated from {n} historical windows{ne_txt} "
                f"({band.get('coverage') or '80% empirical'})."
            )
            if n < 10:
                items.append(
                    "Insufficient windows (n<10): range and calibration unreliable."
                )
            elif n < 30:
                items.append(
                    "Small sample (n<30): range and calibration carry wide uncertainty."
                )
        except (TypeError, ValueError):
            pass
    try:
        spread = result.get("ensemble_spread")
        if isinstance(spread, (int, float)) and not isinstance(spread, bool):
            if float(spread) > 0.15:
                items.append(
                    f"Member disagreement high (spread {float(spread):.2f}): "
                    "treat direction as uncertain even if confidence is moderate."
                )
    except Exception:
        pass
    try:
        reasons = result.get("confidence_reasons") or []
        if isinstance(reasons, list) and reasons:
            items.append("Confidence penalties: " + "; ".join(str(r) for r in reasons[:4]))
    except Exception:
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


#: quantile_bands emits "high"; the forecasts table CHECK allows
#: low/normal/elevated/extreme. Map on persist (display keeps "high").
_REGIME_PERSIST_MAP = {"high": "elevated"}


def _persist_forecast_record(result: dict, symbol: str, market_service=None) -> None:
    """Insert the versioned forecast row + audit event (best-effort, never raises).

    Feeds GET /api/audit/forecasts (homepage "Latest research"). Skipped
    under pytest (PYTEST_CURRENT_TEST) so unit runs never pollute the dev
    sqlite file; production/server always attempts the write. Duplicate
    runs (same instrument+horizon+target+versions UNIQUE key) are ignored.
    """
    try:
        if os.getenv("PYTEST_CURRENT_TEST"):
            return
        from backend.db.session import get_session_factory, init_db

        try:
            init_db()
        except Exception:
            pass
        Session = get_session_factory()
        db = Session()
        try:
            from backend.db.models import Forecast as DBForecast
            from backend.db.models import Instrument as DBInstrument

            # Resolve canonical identity via the registry (never bare ticker).
            # Unknown symbols (no registry entry) are NOT persisted: writing
            # stub forecasts for garbage tickers would pollute instruments.
            mic: str | None = None
            exchange_symbol: str | None = None
            provider_symbol: str | None = None
            company_name = ""
            currency = "USD"
            resolved = False
            try:
                registry = getattr(market_service, "registry", None)
                if registry is not None:
                    inst, _, _ = registry.resolve(symbol)
                    if inst is None:
                        # Valid non-seed tickers (GOOGL/META/...) resolve via
                        # the provisional path so the audit trail is kept.
                        # Alphabet-invalid input yields None and stays
                        # unpersisted (never pollute instruments with stubs).
                        try:
                            from backend.market_data.service import (
                                _provisional_instrument as _prov,
                            )
                            from backend.security.validation import (
                                validate_symbol as _vsym,
                            )

                            _vsym(symbol, field="symbol")
                            inst = _prov(symbol)
                        except Exception:
                            inst = None
                    if inst is not None:
                        resolved = True
                        mic = str(inst.exchange_mic or "").strip().upper() or None
                        exchange_symbol = str(inst.exchange_symbol or "").strip() or None
                        provider_symbol = str(
                            inst.provider_symbol or exchange_symbol or ""
                        ).strip() or None
                        company_name = str(inst.company_name or "")
                        currency = str(inst.currency or "USD").strip().upper() or "USD"
            except Exception:
                pass
            if not resolved or not mic or not exchange_symbol:
                return
            # Find-or-create the DB instrument row (UUID PK; registry id
            # "XNAS-AAPL" is a string key, not the DB key).
            db_inst = (
                db.query(DBInstrument)
                .filter(
                    DBInstrument.exchange_mic == mic,
                    DBInstrument.exchange_symbol == exchange_symbol,
                )
                .first()
            )
            if db_inst is None:
                db_inst = DBInstrument(
                    exchange_mic=mic,
                    exchange_symbol=exchange_symbol,
                    provider_symbol=provider_symbol,
                    company_name=company_name,
                    currency=(currency[:3] if currency else "USD"),
                    trading_calendar=mic,
                    is_active=True,
                )
                db.add(db_inst)
                try:
                    db.flush()
                except Exception:
                    db.rollback()
                    db_inst = (
                        db.query(DBInstrument)
                        .filter(
                            DBInstrument.exchange_mic == mic,
                            DBInstrument.exchange_symbol == exchange_symbol,
                        )
                        .first()
                    )
                    if db_inst is None:
                        return
            # Parse fields from the service record (tolerant, never raises).
            record = result.get("record") if isinstance(result, dict) else None
            record = record if isinstance(record, dict) else {}
            horizon = int(result.get("horizon_days") or record.get("horizon_days") or 0)
            if horizon not in FORECAST_HORIZONS:
                return
            target_raw = result.get("target_date") or record.get("target_date")
            try:
                from datetime import date as _date

                target_date = _date.fromisoformat(str(target_raw)[:10])
            except Exception:
                return
            try:
                direction = result.get("direction_probability")
                direction_f = None if direction is None else float(direction)
            except (TypeError, ValueError):
                direction_f = None
            band = result.get("expected_return_range") or {}
            try:
                ret_low = None if band.get("low") is None else float(band["low"])
                ret_high = None if band.get("high") is None else float(band["high"])
            except (TypeError, ValueError):
                ret_low, ret_high = None, None
            regime_raw = str(result.get("volatility_regime") or "")
            regime = _REGIME_PERSIST_MAP.get(regime_raw, regime_raw) or None
            if regime not in ("low", "normal", "elevated", "extreme"):
                regime = None
            try:
                dd_raw = result.get("drawdown_probability")
                dd = None if dd_raw is None else float(dd_raw)
            except (TypeError, ValueError):
                dd = None
            confidence = str(result.get("confidence") or "")
            if confidence not in ("low", "moderate", "high"):
                # Conservative fallback: unknown labels must not inflate to moderate.
                confidence = "low"
            model_version = str(result.get("model_version") or "")
            feature_version = str(result.get("feature_version") or "")
            data_version = str(result.get("data_version") or "")
            if not (model_version and feature_version and data_version):
                return
            provenance = result.get("provenance")
            provenance = dict(provenance) if isinstance(provenance, dict) else {}
            # Idempotency: same (instrument, horizon, target, versions) run
            # already logged -> skip instead of stacking duplicate rows.
            try:
                existing = (
                    db.query(DBForecast)
                    .filter(
                        DBForecast.instrument_id == db_inst.instrument_id,
                        DBForecast.horizon_days == horizon,
                        DBForecast.target_date == target_date,
                        DBForecast.model_version == model_version,
                        DBForecast.feature_version == feature_version,
                        DBForecast.data_version == data_version,
                    )
                    .first()
                )
                if existing is not None:
                    return
            except Exception:
                pass
            row = DBForecast(
                instrument_id=db_inst.instrument_id,
                horizon_days=horizon,
                target_date=target_date,
                direction_prob=direction_f,
                expected_ret_low=ret_low,
                expected_ret_high=ret_high,
                volatility_regime=regime,
                drawdown_prob=dd,
                confidence=confidence,
                model_version=model_version,
                feature_version=feature_version,
                data_version=data_version,
                ai_provider=None,
                ai_model=None,
                ai_weight=0,
                provenance=provenance,
            )
            db.add(row)
            try:
                db.commit()
            except Exception:
                # Duplicate UNIQUE run or constraint miss -> ignore, keep serving.
                try:
                    db.rollback()
                except Exception:
                    pass
                return
            try:
                db.refresh(row)
            except Exception:
                pass
            # Audit event for the new version (redacted inside; never raises).
            try:
                from backend.api.audit import log_forecast_created

                log_forecast_created(
                    db,
                    forecast_id=str(row.forecast_id),
                    payload={
                        "symbol": (symbol or "").strip().upper(),
                        "horizon_days": horizon,
                        "model_version": model_version,
                        "feature_version": feature_version,
                        "data_version": data_version,
                    },
                    actor="system",
                )
            except Exception:
                pass
        finally:
            try:
                db.close()
            except Exception:
                pass
    except Exception:
        pass
