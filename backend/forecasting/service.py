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
    build_euronext_features,
)
from backend.forecasting.models.historical_drift import HistoricalDriftBaseline
from backend.forecasting.models.logistic import LogisticDirectionModel
from backend.forecasting.models.momentum import MomentumBaseline
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
from backend.forecasting.registry import ENSEMBLE_MEMBERS, ENSEMBLE_VERSION
from backend.market_data.service import MarketDataService

DISCLOSURE = "Not investment advice"
CONFIDENCE_LEVELS = ("low", "moderate", "high")
BAR_LIMIT = 250
SSE_BLEND_VERSION = f"{ENSEMBLE_VERSION}+{SSE_DRIFT_VERSION}"
EUX_BLEND_VERSION = f"{ENSEMBLE_VERSION}+{EUX_DRIFT_VERSION}"
EURONEXT_SUFFIXES = (".PA", ".AS", ".BR")
EURONEXT_MICS = ("XPAR-", "XAMS-", "XBRU-")


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
    elif spread <= 0.08 and n_models >= 3:
        level = "high"
    elif spread <= 0.15:
        level = "moderate"
    else:
        level = "low"
    if n_models < 3 and level == "high":
        level = "moderate"
    if str(quality_grade).upper() in ("D", "F") and level != "low":
        level = "low"
    if regime is not None:
        try:
            _r = str(regime).strip().lower()
        except Exception:
            _r = ""
        if _r in ("high", "elevated", "extreme"):
            level = _penalize_confidence(level)
    _dd = drawdown_prob
    if isinstance(_dd, bool):
        _dd = None
    if isinstance(_dd, (int, float)):
        try:
            if float(_dd) >= 0.25:
                level = _penalize_confidence(level)
        except (TypeError, ValueError):
            pass
    return level


def _target_date(as_of_iso: str, horizon_days: int) -> str:
    try:
        base = datetime.fromisoformat(str(as_of_iso).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        base = datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    return (base + timedelta(days=int(horizon_days))).date().isoformat()


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
        bars = self.market.get_bars(symbol.strip().upper(), timeframe="1d", limit=BAR_LIMIT)
        rows = bars.get("bars", [])
        if len(rows) < 100:
            raise ValueError(f"insufficient history for {symbol!r}: {len(rows)} bars")
        frame = pd.DataFrame(
            {
                "open": [r["open"] for r in rows],
                "high": [r["high"] for r in rows],
                "low": [r["low"] for r in rows],
                "close": [r["close"] for r in rows],
                "volume": [float(r["volume"] or 0) for r in rows],
            },
            index=pd.to_datetime([r["ts"] for r in rows]),
        )
        return frame, bars

    # -- public ---------------------------------------------------------
    def forecast(
        self, symbol: str, horizon: int, as_of: str | None = None
    ) -> dict:
        horizon = int(horizon)
        if horizon not in FORECAST_HORIZONS:
            raise ValueError(f"horizon must be one of {list(FORECAST_HORIZONS)}, got {horizon}")
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
        us_direction = float(sum(probas.values()) / len(probas))
        us_spread = float(max(probas.values()) - min(probas.values())) if probas else 0.0

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
            model_members = list(ENSEMBLE_MEMBERS) + [SSE_DRIFT_VERSION]
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
            try:
                build_euronext_features(ohlcv)
            except ValueError:
                pass
            confidence = _confidence(
                spread,
                len(probas),
                str(provenance.get("quality_grade", "B")),
                regime,
                dd_prob,
            )
            model_version = EUX_BLEND_VERSION
            feature_version = EUX_FEATURE_VERSION
            model_members = list(ENSEMBLE_MEMBERS) + [EUX_DRIFT_VERSION]
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
            model_members = list(ENSEMBLE_MEMBERS)

        instrument_id = bars.get("instrument_id") or f"stub-{symbol.strip().upper()}"
        target_date = _target_date(stamp, horizon)
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
