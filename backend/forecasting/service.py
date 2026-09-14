"""Forecast orchestrator: market_data -> features -> models -> bands (Milestone 3).

Deterministic, no AI, no network. The market-data bars used here are the
offline-capable deterministic stub (seeded by symbol), so runs are
reproducible per symbol/day without any provider call.

Pipeline per (symbol, horizon):
  1. MarketDataService.get_bars(symbol, limit=250) -> OHLCV + provenance.
  2. build_features(OHLCV) (past-only, leakage-safe).
  3. Ensemble direction = mean of available {historical-drift, momentum,
     logistic-direction} probabilities (logistic failure falls back cleanly).
  4. Expected-return range from empirical quantile bands (median -> mid).
  5. Volatility regime + drawdown probability from quantile_bands estimators.
  6. Confidence from member agreement (+ quality-grade downgrade).
  7. Record dict whose keys mirror infra/migrations/0001_initial.sql
     forecasts table columns.

Library code never reads the wall clock for values: `as_of` is taken from
the bars provenance envelope (caller-supplied override wins in tests).
"""

from __future__ import annotations

import hashlib
import math
import uuid
from datetime import datetime, timedelta, timezone

import pandas as pd

from backend.forecasting.common import FORECAST_HORIZONS
from backend.forecasting.features.features import (
    FEATURE_VERSION,
    build_features,
    log_returns,
)
from backend.forecasting.features.sse import (
    SSE_FEATURE_VERSION,
    build_sse_features,
    limit_proximity_triggered,
)
from backend.forecasting.features.euronext import (
    EUX_FEATURE_VERSION,
)
from backend.forecasting.models.historical_drift import HistoricalDriftBaseline
from backend.forecasting.models.historical_drift import (
    MODEL_VERSION as HISTORICAL_DRIFT_VERSION,
)
from backend.forecasting.models.logistic import LogisticDirectionModel
from backend.forecasting.models.logistic import MODEL_VERSION as LOGISTIC_VERSION
from backend.forecasting.models.momentum import MomentumBaseline
from backend.forecasting.models.momentum import MODEL_VERSION as MOMENTUM_VERSION
from backend.forecasting.models.quantile_bands import (
    drawdown_probability,
    return_quantiles,
    volatility_regime,
)
from backend.forecasting.models.sse_drift import (
    MODEL_VERSION as SSE_DRIFT_VERSION,
    SseDriftBaseline,
)
from backend.forecasting.models.euronext_drift import (
    MODEL_VERSION as EUX_DRIFT_VERSION,
    EuxDriftBaseline,
)
from backend.forecasting.registry import ENSEMBLE_VERSION
from backend.market_data.service import MarketDataService

DISCLOSURE = "Not investment advice"
BAR_LIMIT = 250
#: Member-agreement spread (max(p) - min(p)) for full 3-member "high"
#: confidence. Values unchanged; named so the band lives in one place.
CONFIDENCE_HIGH_MAX_SPREAD = 0.08
#: Spread ceiling for "moderate" confidence (above this -> "low").
CONFIDENCE_MODERATE_MAX_SPREAD = 0.15
#: Drawdown probability at/above which confidence drops one notch.
DRAWDOWN_PENALTY_THRESHOLD = 0.25
#: Volatility regimes that cost one confidence notch (strongest bucket first).
VOLATILITY_PENALTY_REGIMES = frozenset({"high", "elevated", "extreme"})
#: Members needed for "high" confidence (thin ensembles cap at moderate).
FULL_ENSEMBLE_MIN_MODELS = 3
SSE_BLEND_VERSION = f"{ENSEMBLE_VERSION}+{SSE_DRIFT_VERSION}"
EUX_BLEND_VERSION = f"{ENSEMBLE_VERSION}+{EUX_DRIFT_VERSION}"
#: Ensemble member key (probas dict) -> stamped model version. Used to derive
#: model_members/evidence_ids from the members that actually ran (a failed
#: logistic fit must not be listed as evidence).
MEMBER_VERSIONS = {
    "historical-drift": HISTORICAL_DRIFT_VERSION,
    "momentum": MOMENTUM_VERSION,
    "logistic-direction": LOGISTIC_VERSION,
    "sse-drift": SSE_DRIFT_VERSION,
    "eux-drift": EUX_DRIFT_VERSION,
}
#: Bound on the in-memory record mirror (prevents unbounded growth on
#: long-lived processes; oldest rows are dropped, newest preserved).
MAX_RECORDS = 500
EURONEXT_SUFFIXES = (".PA", ".AS", ".BR")
EURONEXT_MICS = ("XPAR-", "XAMS-", "XBRU-")


def _finite_or_none(value: object) -> float | None:
    """Return ``float(value)`` when finite, else None (JSON-safe sanitize)."""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _is_sse(symbol: str, bars: dict) -> bool:
    """Route helper: True for Shanghai (XSHG) instruments.

    SSE when the user symbol carries the Yahoo ``.SS`` suffix OR the
    resolved ``instrument_id`` carries the ``XSHG-`` prefix (MIC-based).
    """
    sym = (symbol or "").strip().upper()
    if sym.endswith(".SS"):
        return True
    try:
        inst = str((bars or {}).get("instrument_id") or "").upper()
    except Exception:
        inst = ""
    return inst.startswith("XSHG-")


def _is_euronext(symbol: str, bars: dict) -> bool:
    """Route helper: True for Euronext (XPAR/XAMS/XBRU) instruments.

    Euronext when the user symbol carries a Yahoo ``.PA`` (Paris) /
    ``.AS`` (Amsterdam) / ``.BR`` (Brussels) suffix OR the resolved
    ``instrument_id`` carries an ``XPAR-`` / ``XAMS-`` / ``XBRU-`` prefix
    (MIC-based). Euronext venues share a continuous 09:00-17:30 session
    with no daily price limits, so one ``eux-drift`` path covers all three.
    """
    sym = (symbol or "").strip().upper()
    if sym.endswith(EURONEXT_SUFFIXES):
        return True
    try:
        inst = str((bars or {}).get("instrument_id") or "").upper()
    except Exception:
        inst = ""
    return inst.startswith(EURONEXT_MICS)


def _penalize_confidence(level: str) -> str:
    """Downgrade confidence one notch: high->moderate->low (low stays low)."""
    mapping = {"high": "moderate", "moderate": "low", "low": "low"}
    return mapping.get(str(level), "low")

_service: ForecastService | None = None  # module singleton (see getter below)


def _confidence(
    spread: float,
    n_models: int,
    quality_grade: str,
    regime: str | None = None,
    drawdown_prob: float | None = None,
) -> str:
    """Deterministic confidence from member agreement.

    spread = max(p) - min(p) over ensemble members. Full agreement + 3
    members -> high; moderate agreement -> moderate; else low. Thin
    ensembles (fallback) cap at moderate; poor data quality caps at low.

    Regime-aware penalties (applied AFTER the spread/size/quality logic
    via :func:`_penalize_confidence`, never raising, flooring at "low"):
      * volatility regime "high" (top-tercile trailing vol, the strongest
        bucket quantile_bands emits), "elevated" or "extreme" -> one notch.
      * drawdown probability >= 0.25 -> one notch down.
    ``regime=None`` / ``drawdown_prob=None`` are no-ops (backward compat).
    """
    if n_models <= 1:
        level = "low"
    elif spread <= CONFIDENCE_HIGH_MAX_SPREAD and n_models >= FULL_ENSEMBLE_MIN_MODELS:
        level = "high"
    elif spread <= CONFIDENCE_MODERATE_MAX_SPREAD:
        level = "moderate"
    else:
        level = "low"
    if n_models < FULL_ENSEMBLE_MIN_MODELS and level == "high":
        level = "moderate"
    if str(quality_grade).upper() in ("D", "F") and level != "low":
        level = "low"
    if regime is not None:
        try:
            _r = str(regime).strip().lower()
        except Exception:
            _r = ""
        if _r in VOLATILITY_PENALTY_REGIMES:
            level = _penalize_confidence(level)
    _dd = drawdown_prob
    if isinstance(_dd, bool):
        _dd = None
    if isinstance(_dd, (int, float)):
        try:
            if float(_dd) >= DRAWDOWN_PENALTY_THRESHOLD:
                level = _penalize_confidence(level)
        except (TypeError, ValueError):
            pass
    return level


def _target_mic(symbol: str, bars: dict, sse: bool, eux: bool) -> str:
    """Exchange MIC for trading-day arithmetic (suffix/instrument-derived)."""
    if sse:
        return "XSHG"
    sym = (symbol or "").strip().upper()
    for suffix, mic in ((".PA", "XPAR"), (".AS", "XAMS"), (".BR", "XBRU")):
        if sym.endswith(suffix):
            return mic
    try:
        inst = str((bars or {}).get("instrument_id") or "").upper()
    except Exception:
        inst = ""
    for mic in ("XPAR", "XAMS", "XBRU", "XNYS", "XSHG"):
        if inst.startswith(mic + "-"):
            return mic
    return "XNAS"


def _target_date(as_of_iso: str, horizon_days: int, mic: str = "XNAS") -> str:
    """Advance ``horizon_days`` TRADING days (horizons are trading days).

    Counts sessions via the exchange calendar (weekends/holidays skipped);
    falls back to calendar days only when the calendar is unavailable.
    """
    try:
        base = datetime.fromisoformat(str(as_of_iso).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        base = datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    try:
        horizon = int(horizon_days)
    except (TypeError, ValueError):
        horizon = 0
    if horizon <= 0:
        return base.date().isoformat()
    try:
        from backend.instruments.calendars import _lib_is_session

        day = base.date()
        counted, guard = 0, 0
        while counted < horizon and guard < horizon * 4 + 30:
            guard += 1
            day += timedelta(days=1)
            session = _lib_is_session(day, mic)
            if session is None:
                # Calendar unavailable (e.g. XSHG stub decision): weekdays
                # approximate sessions; still closer than calendar days.
                if day.weekday() < 5:
                    counted += 1
            elif session:
                counted += 1
        if counted >= horizon:
            return day.isoformat()
    except Exception:
        pass
    return (base + timedelta(days=horizon)).date().isoformat()


def _data_version(provenance: dict) -> str:
    source = str(provenance.get("source", "unknown"))
    as_of = str(provenance.get("as_of", "unknown"))
    day = as_of[:10] if len(as_of) >= 10 else as_of
    return f"{source}-bars-{day}"


def _deterministic_id(symbol: str, horizon: int, as_of: str, data_version: str) -> str:
    material = "|".join([symbol.upper(), str(horizon), as_of, data_version])
    digest = hashlib.sha256(material.encode()).hexdigest()
    return str(uuid.UUID(digest[:32]))


class ForecastService:
    """Stateless orchestrator (an in-memory record list mirrors the DB table)."""

    def __init__(self, market_service: MarketDataService | None = None) -> None:
        self.market = market_service or MarketDataService()
        self.records: list[dict] = []

    # -- internals ------------------------------------------------------
    def _load(self, symbol: str) -> tuple[pd.DataFrame, dict]:
        # Harden: validate symbol early (ValueError, never AttributeError)
        # and coerce malformed bar rows to ValueError (never KeyError).
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError(f"symbol must be a non-empty string, got {symbol!r}")
        bars = self.market.get_bars(symbol.strip().upper(), timeframe="1d", limit=BAR_LIMIT)
        rows = bars.get("bars", [])
        if len(rows) < 100:
            raise ValueError(f"insufficient history for {symbol!r}: {len(rows)} bars")
        try:
            opens = [r["open"] for r in rows]
            highs = [r["high"] for r in rows]
            lows = [r["low"] for r in rows]
            closes = [r["close"] for r in rows]
            volumes = [float((r.get("volume") if isinstance(r, dict) else None) or 0) for r in rows]
            stamps = [(r.get("ts") if isinstance(r, dict) else None) for r in rows]
            index = pd.to_datetime(stamps)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"malformed bars for {symbol!r}: {exc}") from exc
        except Exception as exc:
            raise ValueError(f"malformed bars for {symbol!r}: {type(exc).__name__}") from exc
        try:
            frame = pd.DataFrame(
                {
                    "open": opens,
                    "high": highs,
                    "low": lows,
                    "close": closes,
                    "volume": volumes,
                },
                index=index,
            )
        except (ValueError, TypeError) as exc:
            raise ValueError(f"malformed bars for {symbol!r}: {exc}") from exc
        return frame, bars

    # -- public ---------------------------------------------------------
    def forecast(
        self, symbol: str, horizon: int, as_of: str | None = None
    ) -> dict:
        try:
            horizon = int(horizon)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ValueError(f"horizon must be one of {list(FORECAST_HORIZONS)}, got {horizon!r}") from exc
        if horizon not in FORECAST_HORIZONS:
            raise ValueError(f"horizon must be one of {list(FORECAST_HORIZONS)}, got {horizon}")
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError(f"symbol must be a non-empty string, got {symbol!r}")
        ohlcv, bars = self._load(symbol)
        provenance = dict(bars.get("provenance", {}))
        stamp = as_of or str(provenance.get("as_of"))
        base_data_version = _data_version({**provenance, "as_of": stamp})
        sse = _is_sse(symbol, bars)
        eux = _is_euronext(symbol, bars) and not sse
        if sse:
            data_version = f"{base_data_version}-sse"
        elif eux:
            data_version = f"{base_data_version}-eux"
        else:
            data_version = base_data_version
        closes = ohlcv["close"]
        features = build_features(ohlcv)

        # -- direction ensemble (drift + momentum + logistic) ------------
        lret = log_returns(closes).dropna()
        drift_p = float(
            HistoricalDriftBaseline()
            .fit(lret)
            .direction_probability(horizon, as_of=stamp, data_version=data_version)
            .value
        )
        momentum_p = float(
            MomentumBaseline()
            .fit(closes)
            .direction_probability(horizon, as_of=stamp, data_version=data_version)
            .value
        )
        probas: dict[str, float] = {
            "historical-drift": drift_p,
            "momentum": momentum_p,
        }
        formulas: dict[str, str] = {}
        try:
            logreg = LogisticDirectionModel(horizons=[horizon]).fit(features, closes)
            logistic_p = float(
                logreg.predict_direction_proba(
                    features.iloc[[-1]], as_of=stamp, data_version=data_version
                )[horizon].value
            )
            probas["logistic-direction"] = logistic_p
        except (ValueError, ImportError):
            pass  # single-class / too-few-rows / sklearn missing: drift+momentum
        members = sorted(probas)
        if probas:
            us_direction = float(sum(probas.values()) / len(probas))
            us_spread = float(max(probas.values()) - min(probas.values()))
        else:  # defensive: fits below always seed >= 2 members; never empty
            us_direction, us_spread = 0.5, 0.0

        # -- return range / regime / drawdown ----------------------------
        band = return_quantiles(
            closes, horizons=[horizon], as_of=stamp, data_version=data_version
        )[horizon].value
        regime_res = volatility_regime(closes, as_of=stamp, data_version=data_version)
        dd_res = drawdown_probability(
            closes, horizon, as_of=stamp, data_version=data_version
        )
        us_range = {
            "low": float(band["low"]),
            "mid": float(band["median"]),
            "high": float(band["high"]),
            "lower_q": float(band["lower_q"]),
            "upper_q": float(band["upper_q"]),
            "n_windows": int(band["n_windows"]),
        }
        regime = str(regime_res.value["regime"])
        dd_prob = float(dd_res.value["probability"])

        if sse:
            # -- SSE path: blend US ensemble 50/50 with SSE drift ---------
            # sse-drift sees the same trailing log-returns (winsorized
            # internally to the +/-10% limit band) with wider tails.
            sse_model = SseDriftBaseline().fit(lret)
            sse_p = float(
                sse_model.direction_probability(
                    horizon, as_of=stamp, data_version=data_version
                ).value
            )
            probas["sse-drift"] = sse_p
            direction = float((us_direction + sse_p) / 2.0)
            spread = float(max(probas.values()) - min(probas.values())) if probas else 0.0
            # Union of ranges: conservative envelope of the US empirical
            # band and the wider SSE drift band; mid is the mean of mids.
            sse_band = sse_model.expected_return_range(
                horizon, as_of=stamp, data_version=data_version
            ).value
            expected_range = {
                "low": float(min(us_range["low"], sse_band["low"])),
                "mid": float((us_range["mid"] + float(sse_band["mid"])) / 2.0),
                "high": float(max(us_range["high"], sse_band["high"])),
                "lower_q": float(us_range["lower_q"]),
                "upper_q": float(us_range["upper_q"]),
                "n_windows": int(us_range["n_windows"]),
            }
            try:
                sse_features = build_sse_features(ohlcv)
                proximity_fired = limit_proximity_triggered(sse_features)
            except ValueError:
                proximity_fired = False
            base_confidence = _confidence(
                spread,
                len(probas),
                str(provenance.get("quality_grade", "B")),
                regime,
                dd_prob,
            )
            confidence = (
                _penalize_confidence(base_confidence)
                if proximity_fired
                else base_confidence
            )
            model_version = SSE_BLEND_VERSION
            feature_version = SSE_FEATURE_VERSION
        elif eux:
            # -- Euronext path: blend US ensemble 50/50 with eux drift --
            # eux-drift sees the same trailing log-returns (winsorized
            # internally to the +/-15% robustness cap; Euronext has no
            # hard daily limits) with a z=1.15 band. No limit-proximity
            # penalty applies (no limits); Euronext features are built to
            # stamp the eux feature version.
            eux_model = EuxDriftBaseline().fit(lret)
            eux_p = float(
                eux_model.direction_probability(
                    horizon, as_of=stamp, data_version=data_version
                ).value
            )
            probas["eux-drift"] = eux_p
            direction = float((us_direction + eux_p) / 2.0)
            spread = float(max(probas.values()) - min(probas.values())) if probas else 0.0
            # Union of ranges: conservative envelope of the US empirical
            # band and the eux drift band; mid is the mean of mids.
            eux_band = eux_model.expected_return_range(
                horizon, as_of=stamp, data_version=data_version
            ).value
            expected_range = {
                "low": float(min(us_range["low"], eux_band["low"])),
                "mid": float((us_range["mid"] + float(eux_band["mid"])) / 2.0),
                "high": float(max(us_range["high"], eux_band["high"])),
                "lower_q": float(us_range["lower_q"]),
                "upper_q": float(us_range["upper_q"]),
                "n_windows": int(us_range["n_windows"]),
            }
            # NOTE: the EUX feature frame is version-stamped via
            # EUX_FEATURE_VERSION above; its values are not consumed by the
            # drift blend, so no second feature build is needed here.
            confidence = _confidence(
                spread,
                len(probas),
                str(provenance.get("quality_grade", "B")),
                regime,
                dd_prob,
            )
            model_version = EUX_BLEND_VERSION
            feature_version = EUX_FEATURE_VERSION
        else:
            direction = us_direction
            spread = us_spread
            expected_range = us_range
            confidence = _confidence(
                spread,
                len(probas),
                str(provenance.get("quality_grade", "B")),
                regime,
                dd_prob,
            )
            model_version = ENSEMBLE_VERSION
            feature_version = FEATURE_VERSION

        # -- JSON safety: non-finite floats are invalid JSON (NaN/inf) ----
        # Finite inputs pass through bit-identical; only pathological model
        # outputs (e.g. exp() overflow on extreme synthetic drift) map to
        # None instead of leaking NaN/inf to the wire/DB.
        _clean_direction = _finite_or_none(direction)
        if _clean_direction is None:
            raise ValueError("non-finite direction probability")
        direction = _clean_direction
        dd_prob = _finite_or_none(dd_prob)
        for _bound in ("low", "mid", "high"):
            expected_range[_bound] = _finite_or_none(expected_range.get(_bound))
        for _name, _value in list(probas.items()):
            _clean_member = _finite_or_none(_value)
            # JSON safety: non-finite members sanitize to None (never NaN/inf).
            # Reachable only on pathological model output; direction already
            # validated finite above, so this never changes valid ensembles.
            probas[_name] = _clean_member
        # Evidence = members that actually ran (a failed/skipped fit is never
        # listed: its version would otherwise render as contributing evidence).
        model_members = [
            MEMBER_VERSIONS[name]
            for name in sorted(probas)
            if probas[name] is not None and name in MEMBER_VERSIONS
        ]

        instrument_id = bars.get("instrument_id") or f"stub-{symbol.strip().upper()}"
        target_date = _target_date(
            stamp, horizon, _target_mic(symbol, bars, sse, eux)
        )
        record = {
            "forecast_id": _deterministic_id(symbol, horizon, stamp, data_version),
            "instrument_id": instrument_id,
            "horizon_days": horizon,
            "target_date": target_date,
            "direction_prob": direction,
            "expected_ret_low": expected_range["low"],
            "expected_ret_high": expected_range["high"],
            "volatility_regime": regime,
            "drawdown_prob": dd_prob,
            "confidence": confidence,
            "model_version": model_version,
            "feature_version": feature_version,
            "data_version": data_version,
            "ai_provider": None,
            "ai_model": None,
            "ai_weight": 0,
            "provenance": provenance,
            "created_at": stamp,
        }
        self.records.append(record)
        if len(self.records) > MAX_RECORDS:
            del self.records[: len(self.records) - MAX_RECORDS]

        return {
            "symbol": symbol.strip().upper(),
            "horizon_days": horizon,
            "direction_probability": direction,
            "expected_return_range": expected_range,
            "volatility_regime": regime,
            "drawdown_probability": dd_prob,
            "confidence": confidence,
            "model_version": model_version,
            "model_members": model_members,
            "components": dict(probas),
            "formulas": dict(formulas),
            "feature_version": feature_version,
            "data_version": data_version,
            "as_of": stamp,
            "target_date": target_date,
            "provenance": provenance,
            "disclosure": DISCLOSURE,
            "record": record,
        }

    def forecast_all(self, symbol: str, as_of: str | None = None) -> dict[int, dict]:
        return {h: self.forecast(symbol, h, as_of=as_of) for h in FORECAST_HORIZONS}


def get_forecast_service() -> ForecastService:
    global _service
    if _service is None:
        _service = ForecastService()
    return _service


def reset_forecast_service() -> None:  # test hook
    global _service
    _service = None


__all__ = [
    "DISCLOSURE",
    "ForecastService",
    "get_forecast_service",
    "reset_forecast_service",
]
