"""Persist each served forecast to the versioned ``forecasts`` log.

Feeds the audit trail and the forward accuracy scoring (``score`` cron job),
which compares every logged forecast with what actually happened.
"""

from __future__ import annotations

from backend.forecasting.common import FORECAST_HORIZONS


#: The forecasts table CHECK allows low/normal/elevated/extreme; the engine
#: says "high". Map on persist (display keeps "high").
_REGIME_PERSIST_MAP = {"high": "elevated"}


def persist_forecast_record(result: dict, symbol: str, market_service=None) -> None:
    """Insert the versioned forecast row + audit event (best-effort, never raises).

    Feeds GET /api/audit/forecasts (homepage "Latest research"). Duplicate
    runs (same instrument+horizon+target+versions UNIQUE key) are ignored.
    """
    try:
        from backend.db.session import get_session_factory, ensure_schema

        try:
            ensure_schema()
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
