"""Forecast orchestrator: market_data -> features -> models -> bands (ensemble-v3).

Deterministic, no AI, no network. The market-data bars used here are the
offline-capable deterministic stub (seeded by symbol), so runs are
reproducible per symbol/day without any provider call.

Pipeline per (symbol, horizon):
  1. MarketDataService.get_bars(symbol, limit=500) -> OHLCV + provenance.
  2. build_features(OHLCV) (past-only, leakage-safe).
  3. Ensemble-v3 direction = adaptive (inverse-Brier) or fixed mean of
     available {historical-drift, momentum, logistic-direction,
     gradient-boost-direction, trend-persistence, mean-reversion}
     probabilities (ML failure falls back cleanly), then isotonic/Platt
     (or shrinkage) calibration.
  4. Expected-return range from empirical quantile bands (median -> mid).
  5. Volatility regime + drawdown probability from quantile_bands estimators.
  6. Confidence from member agreement + trailing skill (Brier/ECE/
     n_effective) + quality-grade downgrades.
  7. Record dict whose keys mirror infra/migrations/0001_initial.sql
     forecasts table columns.

Library code never reads the wall clock for values: `as_of` is taken from
the bars provenance envelope (caller-supplied override wins in tests).
"""

from __future__ import annotations

import hashlib
import math
import time
import uuid
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import pandas as pd

from backend.forecasting.common import FORECAST_HORIZONS
from backend.forecasting.features.features import (
    EXTENDED_FEATURE_VERSION,
    FEATURE_VERSION,
    build_extended_features,
    build_feature_bundle,
    build_features,
    clear_feature_cache,
    corporate_action_flags,
    log_returns,
    thin_liquidity_flags,
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
from backend.forecasting.models.gradient_boost import GradientBoostDirectionModel
from backend.forecasting.models.gradient_boost import (
    MODEL_VERSION as GRADIENT_BOOST_VERSION,
)
from backend.forecasting.models.historical_drift import FORMULA_DIRECTION as DRIFT_FORMULA
from backend.forecasting.models.logistic import FORMULA as LOGISTIC_FORMULA
from backend.forecasting.models.logistic import LogisticDirectionModel
from backend.forecasting.models.logistic import MODEL_VERSION as LOGISTIC_VERSION
from backend.forecasting.models.momentum import FORMULA as MOMENTUM_FORMULA
from backend.forecasting.models.momentum import MomentumBaseline
from backend.forecasting.models.momentum import MODEL_VERSION as MOMENTUM_VERSION
from backend.forecasting.models.gradient_boost import FORMULA as GB_FORMULA
from backend.forecasting.models.quantile_bands import (
    FORMULA_BANDS,
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
from backend.forecasting.registry import (
    ENSEMBLE_VERSION,
    MEAN_REVERSION_VERSION,
    TREND_PERSISTENCE_VERSION,
)
from backend.market_data.service import MarketDataService

DISCLOSURE = "Not investment advice"
BAR_LIMIT = 500
#: ensemble-v3 fixed reliability weights (sum 1.0; renormalized over the
#: members that actually ran). ML members get 0.22 each (nonlinear + linear
#: capture different structure), drift/momentum 0.18 each (base-rate priors),
#: trend-persistence + mean-reversion 0.10 each (heuristics, lowest weight).
#: Fixed weights are the fallback; per-symbol adaptive inverse-Brier weights
#: (from trailing snapshots) plug in via _weighted_mean(..., member_brier).
ENSEMBLE_WEIGHTS = {
    "historical-drift": 0.18,
    "momentum": 0.18,
    "logistic-direction": 0.22,
    "gradient-boost-direction": 0.22,
    "trend-persistence": 0.10,
    "mean-reversion": 0.10,
}
#: Shrinkage toward 0.5 applied when no isotonic/Platt calibrator is
#: available (damps overconfidence; raw ML/drift means are typically
#: overconfident on 500-bar fits).
#: p_cal = 0.5 + (p_raw - 0.5) * SHRINKAGE, then clipped to [FLOOR, CAP].
#: With a fitted calibrator dict, _calibrate_prob applies it instead.
CALIBRATION_SHRINKAGE = 0.8
PROB_FLOOR = 0.05
PROB_CAP = 0.95
#: Skill gates for the v3 confidence honesty layer (see _confidence):
#: ensemble Brier above coin-flip or ECE above this costs a notch.
SKILL_BRIER_THRESHOLD = 0.25
SKILL_ECE_THRESHOLD = 0.15
#: Effective independent blocks below this costs a notch (overlapping
#: h-day windows: n_effective ~= n_windows / horizon).
SKILL_MIN_N_EFFECTIVE = 5.0
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
#: ensemble-v3 has 6 members; require >= 5 for high (one ML miss tolerated).
FULL_ENSEMBLE_MIN_MODELS = 5
#: Minimum distance of the ensemble mean from 0.5 for "high" confidence.
#: Agreement near coin-flip (e.g. members {0.51,0.53,0.55}) must not read
#: as high-confidence even when spread is tight.
CONFIDENCE_HIGH_MIN_DISTANCE = 0.07
#: AI disagreement above this downgrades the label one notch (mirrors
#: backend.ai.blend.DISAGREEMENT_THRESHOLD; duplicated to avoid a cycle).
AI_DISAGREEMENT_THRESHOLD = 0.10
SSE_BLEND_VERSION = f"{ENSEMBLE_VERSION}+{SSE_DRIFT_VERSION}"
EUX_BLEND_VERSION = f"{ENSEMBLE_VERSION}+{EUX_DRIFT_VERSION}"
#: Ensemble member key (probas dict) -> stamped model version. Used to derive
#: model_members/evidence_ids from the members that actually ran (a failed
#: logistic fit must not be listed as evidence).
MEMBER_VERSIONS = {
    "historical-drift": HISTORICAL_DRIFT_VERSION,
    "momentum": MOMENTUM_VERSION,
    "logistic-direction": LOGISTIC_VERSION,
    "gradient-boost-direction": GRADIENT_BOOST_VERSION,
    "trend-persistence": TREND_PERSISTENCE_VERSION,
    "mean-reversion": MEAN_REVERSION_VERSION,
    "sse-drift": SSE_DRIFT_VERSION,
    "eux-drift": EUX_DRIFT_VERSION,
}
#: member key -> human formula (fills the wire `formulas` dict; v1 left it
#: empty {} which broke the evidence contract).
MEMBER_FORMULAS = {
    "historical-drift": DRIFT_FORMULA,
    "momentum": MOMENTUM_FORMULA,
    "logistic-direction": LOGISTIC_FORMULA,
    "gradient-boost-direction": GB_FORMULA,
    "trend-persistence": (
        "z = (mom_63/vol_63)*2 + trail_dd_63*2 + ((rsi_14-50)/50)*0.5; "
        "P = sigmoid(clip(z, -6, 6)) (63d trend + drawdown + RSI persistence)"
    ),
    "mean-reversion": (
        "z = ((50-rsi_14)/50)*1.0 + (-ret_5/(vol_daily*sqrt(5)))*0.5; "
        "P = sigmoid(clip(z, -6, 6)) (RSI + 5d reversal contrarian)"
    ),
}


def _adaptive_weights(
    available: list[str], member_brier: dict[str, float | None] | None
) -> dict[str, float] | None:
    """Inverse-Brier weights over ``available``; None when unusable.

    Deterministic, never raises. Falls back to fixed ENSEMBLE_WEIGHTS when
    fewer than 2 members carry finite Brier in (0, 1], or when the Brier
    dict is missing. Single usable member is NOT enough to go adaptive
    (would pin 1.0 on one model); caller keeps fixed weights instead.
    """
    try:
        if not isinstance(member_brier, dict) or not member_brier:
            return None
        inv: dict[str, float] = {}
        for name in available:
            try:
                b = member_brier.get(name)
            except Exception:
                continue
            if b is None or isinstance(b, bool):
                continue
            try:
                v = float(b)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(v) or v <= 1e-9 or v > 1.0:
                continue
            inv[str(name)] = 1.0 / v
        if len(inv) < 2:
            return None
        # Only go adaptive when every available member has skill data;
        # partial skill would silently zero-weight the unknown members.
        if set(inv) != set(available):
            return None
        total = sum(inv.values())
        if not total > 0 or not math.isfinite(total):
            return None
        return {k: v / total for k, v in inv.items()}
    except Exception:
        return None


def _weighted_mean(
    probas: dict[str, float | None],
    member_brier: dict[str, float | None] | None = None,
) -> tuple[float, dict[str, float], float, float]:
    """Weighted mean over available members (deterministic).

    Fixed ENSEMBLE_WEIGHTS by default, renormalized over members that ran.
    Pass ``member_brier`` (trailing per-member Brier from snapshots) for
    inverse-Brier adaptive weighting; unusable skill falls back to fixed
    weights so the path never crashes and old callers are unaffected.

    Returns (mean_raw, weights_used, spread, std). Missing/None/NaN members
    are skipped and remaining weights renormalized proportionally. Empty
    input -> (0.5, {}, 0.0, 0.0) defensive fallback (callers always seed
    >= 2 members, so this is unreachable in practice).
    """
    clean: dict[str, float] = {}
    for name, value in (probas or {}).items():
        if value is None or isinstance(value, bool):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            clean[name] = min(max(number, 0.0), 1.0)
    if not clean:
        return 0.5, {}, 0.0, 0.0
    adaptive = _adaptive_weights(list(clean), member_brier)
    if adaptive is not None:
        weights = adaptive
    else:
        total_w = sum(float(ENSEMBLE_WEIGHTS.get(n, 0.0)) for n in clean)
        if not total_w > 0:
            # Unknown member keys only: fall back to equal weight (never crash).
            equal = 1.0 / len(clean)
            weights = {n: equal for n in clean}
        else:
            weights = {n: float(ENSEMBLE_WEIGHTS.get(n, 0.0)) / total_w for n in clean}
    mean = sum(clean[n] * weights[n] for n in clean)
    values = list(clean.values())
    spread = float(max(values) - min(values)) if len(values) > 1 else 0.0
    if len(values) > 1:
        avg = sum(values) / len(values)
        std = math.sqrt(sum((v - avg) ** 2 for v in values) / len(values))
    else:
        std = 0.0
    return float(mean), weights, spread, float(std)


def _calibrate_prob(p_raw: float, calibrator: dict | None = None) -> float:
    """Calibrate a raw ensemble mean (deterministic, never raises).

    With a fitted ``calibrator`` dict (isotonic/Platt from walk-forward
    OOF pairs) applies it; otherwise the v2 shrinkage fallback:
    p_cal = 0.5 + (p_raw - 0.5) * SHRINKAGE, clipped to [FLOOR, CAP].
    """
    if isinstance(calibrator, dict) and calibrator:
        try:
            from backend.forecasting.calibration.calibrators import (
                apply_calibrator as _apply,
            )

            return float(_apply(p_raw, calibrator))
        except Exception:
            pass
    try:
        raw = float(p_raw)
    except (TypeError, ValueError):
        return 0.5
    if not math.isfinite(raw):
        return 0.5
    raw = min(max(raw, 0.0), 1.0)
    cal = 0.5 + (raw - 0.5) * CALIBRATION_SHRINKAGE
    return min(max(cal, PROB_FLOOR), PROB_CAP)


def _confidence_score(spread: float, penalties: int) -> float:
    """Numeric confidence in [0, 1]: 1 - spread/0.25 minus 0.15 per penalty."""
    try:
        base = 1.0 - min(max(float(spread), 0.0) / 0.25, 1.0)
    except (TypeError, ValueError):
        base = 0.0
    score = base - 0.15 * max(int(penalties), 0)
    return min(max(score, 0.0), 1.0)
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

#: Short-TTL in-process forecast cache: (SYMBOL, horizon, data_version) ->
#: (expires_monotonic, payload). Avoids recomputing the 3-model ensemble on
#: horizon tab switches / screener re-renders. Bounded to prevent growth.
_FORECAST_CACHE: dict[tuple[str, int, str], tuple[float, dict]] = {}
_FORECAST_CACHE_TTL_S = 300
_FORECAST_CACHE_MAX = 500


def _forecast_cache_get(symbol: str, horizon: int, data_version: str) -> dict | None:
    try:
        key = (str(symbol).strip().upper(), int(horizon), str(data_version))
    except Exception:
        return None
    try:
        hit = _FORECAST_CACHE.get(key)
    except Exception:
        return None
    if not hit:
        return None
    expires, payload = hit
    if expires < time.monotonic():
        try:
            _FORECAST_CACHE.pop(key, None)
        except Exception:
            pass
        return None
    return payload


def _forecast_cache_set(symbol: str, horizon: int, data_version: str, payload: dict) -> None:
    try:
        key = (str(symbol).strip().upper(), int(horizon), str(data_version))
    except Exception:
        return
    try:
        if len(_FORECAST_CACHE) >= _FORECAST_CACHE_MAX:
            oldest = next(iter(_FORECAST_CACHE))
            _FORECAST_CACHE.pop(oldest, None)
        _FORECAST_CACHE[key] = (time.monotonic() + _FORECAST_CACHE_TTL_S, payload)
    except Exception:
        pass


def clear_forecast_cache() -> None:  # test hook
    try:
        _FORECAST_CACHE.clear()
    except Exception:
        pass
    try:
        clear_feature_cache()
    except Exception:
        pass
    try:
        _cached_target_date.cache_clear()
    except Exception:
        pass


def _cap_stub_confidence(level: str, provenance: dict) -> str:
    """Cap overconfident labels on synthetic/stale data (accuracy honesty).

    Stub bars (fallback_used=True, grade C) can otherwise serve "high"
    confidence from a seeded random walk. Cap stub/high-stale at moderate;
    D/F still cap at low inside :func:`_confidence`.
    """
    try:
        prov = provenance or {}
        fallback = bool(prov.get("fallback_used"))
        grade = str(prov.get("quality_grade") or "").upper()
    except Exception:
        return level
    if (fallback or grade == "C") and level == "high":
        return "moderate"
    return level


def _corporate_action_assessment(closes) -> tuple[bool, list[str]]:
    """Lineage screen: unadjusted splits masquerade as +-50-100% day moves.

    Bars are assumed split/dividend-adjusted upstream (ingest auto_adjust).
    A single-day SIMPLE drop <= -45% (unadjusted 2:1 split signature) or
    jump >= +90% (unadjusted 1:2 reverse-split signature) flags the lineage
    and hard-caps confidence at low. Vectorized via
    :func:`corporate_action_flags`; never raises (unknown -> no flag).
    """
    try:
        flags = corporate_action_flags(closes)
    except Exception:
        return False, []
    notes: list[str] = []
    hard = False
    try:
        if flags.get("has_crash_drop"):
            notes.append(
                "possible unadjusted corporate action (single-day drop <= -45%)")
            hard = True
        if flags.get("has_jump"):
            notes.append(
                "possible unadjusted corporate action (single-day jump >= +90%)")
            hard = True
    except Exception:
        pass
    return hard, notes


def _thin_liquidity_note(ohlcv) -> str | None:
    """Thin-liquidity screen for the confidence penalty (never raises).

    Estimates from non-trading stretches (halts, illiquid names, stub gaps)
    deserve less confidence: one notch via :func:`_penalize_confidence`
    (floor low), with the reason disclosed in provenance missing_fields.
    """
    try:
        volume = ohlcv["volume"]
    except Exception:
        return None
    try:
        flags = thin_liquidity_flags(volume)
    except Exception:
        return None
    try:
        if flags.get("is_thin"):
            return str(flags.get("reason") or "thin liquidity")
    except Exception:
        return None
    return None


def _trend_persistence_signal(ohlcv: pd.DataFrame, _ext: pd.DataFrame | None = None) -> float | None:
    """4th ensemble member from past market data (trend persistence).

    Past-only: 63d trailing return scaled by 63d volatility + drawdown
    penalty + RSI persistence. Deterministic, bounded [0, 1], None when
    history is too short or non-finite. Never raises.

    ``_ext`` accepts a precomputed extended-feature frame (from
    :func:`build_feature_bundle`) so callers that already built features
    skip the rebuild; values are identical either way (same formulas).
    """
    try:
        ext = _ext if _ext is not None else build_extended_features(ohlcv)
    except Exception:
        return None
    try:
        if len(ext) == 0:
            return None
        last = ext.iloc[-1]
        mom = float(last.get("mom_63", float("nan")))
        vol = float(last.get("vol_63", float("nan")))
        dd = float(last.get("trail_dd_63", 0.0) or 0.0)
        rsi = float(last.get("rsi_14", 50.0) or 50.0)
    except Exception:
        return None
    try:
        import math as _math

        if not (_math.isfinite(mom) and _math.isfinite(vol)):
            return None
        scale = vol if vol > 1e-6 else 0.2
        # Risk-adjusted quarterly drift + drawdown drag + RSI tilt.
        z = (mom / scale) * 2.0 + (dd * 2.0) + ((rsi - 50.0) / 50.0) * 0.5
        z = max(min(z, 6.0), -6.0)
        return 1.0 / (1.0 + _math.exp(-z))
    except Exception:
        return None


def _mean_reversion_signal(ohlcv: pd.DataFrame, _ext: pd.DataFrame | None = None) -> float | None:
    """6th ensemble member (contrarian: RSI + 5d reversal).

    Past-only, deterministic, bounded [0, 1], None when history is too
    short or non-finite. Never raises. Diversifies the trend-following
    members (drift/momentum/trend-persistence): oversold + recent drop
    reads bullish, overbought + recent jump reads bearish.

    ``_ext`` accepts a precomputed extended-feature frame (same pattern as
    :func:`_trend_persistence_signal`); values identical either way.
    """
    try:
        ext = _ext if _ext is not None else build_extended_features(ohlcv)
    except Exception:
        return None
    try:
        if len(ext) == 0:
            return None
        last = ext.iloc[-1]
        rsi = float(last.get("rsi_14", 50.0) or 50.0)
        ret5 = float(last.get("ret_5", 0.0) or 0.0)
        vol_ann = float(last.get("vol_21", float("nan")))
    except Exception:
        return None
    try:
        import math as _math

        if not (_math.isfinite(rsi) and _math.isfinite(ret5)):
            return None
        # Annualized vol_21 -> daily; guard flat history.
        if _math.isfinite(vol_ann) and vol_ann > 1e-6:
            vol_daily = vol_ann / _math.sqrt(252.0)
        else:
            vol_daily = 0.01
        denom = vol_daily * _math.sqrt(5.0)
        if not _math.isfinite(denom) or denom <= 1e-9:
            denom = 0.02
        # RSI contrarian tilt + standardized 5d reversal.
        z = ((50.0 - rsi) / 50.0) * 1.0 + (-ret5 / denom) * 0.5
        z = max(min(z, 6.0), -6.0)
        return 1.0 / (1.0 + _math.exp(-z))
    except Exception:
        return None


def _confidence(
    spread: float,
    n_models: int,
    quality_grade: str,
    regime: str | None = None,
    drawdown_prob: float | None = None,
    direction_prob: float | None = None,
    mean_prob: float | None = None,
    ai_disagreement: float | None = None,
    skill_brier: float | None = None,
    skill_ece: float | None = None,
    n_effective: float | None = None,
) -> str:
    """Deterministic confidence from member agreement + trailing skill.

    spread = max(p) - min(p) over ensemble members. Full agreement + 5
    members -> high; moderate agreement -> moderate; else low. Thin
    ensembles (fallback) cap at moderate; poor data quality caps at low.

    Regime-aware penalties (applied AFTER the spread/size/quality logic
    via :func:`_penalize_confidence`, never raising, flooring at "low"):
      * volatility regime "high" (top-tercile trailing vol, the strongest
        bucket quantile_bands emits), "elevated" or "extreme" -> one notch.
      * drawdown probability >= 0.25 -> one notch down (NaN -> penalize,
        conservative: a missing risk signal must not read as safe).
      * unknown/missing quality grade -> one notch down (conservative;
        only A/B/C pass through untouched, D/F cap at low).
      * sharpness: "high" additionally requires |mean(p) - 0.5| >=
        CONFIDENCE_HIGH_MIN_DISTANCE, else capped at moderate. ``None``
        (legacy callers) skips the gate for backward compat.
      * ai_disagreement > AI_DISAGREEMENT_THRESHOLD -> one notch down
        (wires AI second-opinion clash into the displayed label).
      * v3 skill honesty (all ``None``-safe no-ops for legacy callers):
        trailing ensemble Brier >= 0.25 (no better than coin-flip),
        ECE >= 0.15, or n_effective < 5 each cost one notch. These wire
        walk-forward skill (not just agreement) into the label.
    ``regime=None`` / ``drawdown_prob=None`` are no-ops (backward compat).

    NOTE (future per-user calibration hook): per-user / per-tier confidence
    shaping (e.g. learned agreement thresholds) must plug in HERE as a pure
    post-processing step on (level, spread, provenance) — behind auth/tiers
    owned by another agent. This function stays global-deterministic: no
    user identity, no wall clock, no I/O. Do NOT implement auth/tiers here.
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
    try:
        _g = str(quality_grade or "").strip().upper()
    except Exception:
        _g = ""
    if _g in ("D", "F"):
        if level != "low":
            level = "low"
    elif _g not in ("A", "B", "C"):
        # Unknown/missing provenance grade: conservative one-notch penalty.
        level = _penalize_confidence(level)
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
            _dd_f = float(_dd)
        except (TypeError, ValueError):
            _dd_f = None
        if _dd_f is not None:
            if math.isnan(_dd_f):
                level = _penalize_confidence(level)
            elif _dd_f >= DRAWDOWN_PENALTY_THRESHOLD:
                level = _penalize_confidence(level)
    # Sharpness gate: high agreement at coin-flip is not high confidence.
    _mp = mean_prob if mean_prob is not None else direction_prob
    if level == "high" and _mp is not None and not isinstance(_mp, bool):
        try:
            _mp_f = float(_mp)
        except (TypeError, ValueError):
            _mp_f = None
        if _mp_f is not None and math.isfinite(_mp_f):
            if abs(_mp_f - 0.5) < CONFIDENCE_HIGH_MIN_DISTANCE:
                level = _penalize_confidence(level)
    # AI disagreement penalty (second-opinion risk signal).
    _ad = ai_disagreement
    if isinstance(_ad, bool):
        _ad = None
    if isinstance(_ad, (int, float)):
        try:
            _ad_f = float(_ad)
        except (TypeError, ValueError):
            _ad_f = None
        if _ad_f is not None and math.isfinite(_ad_f):
            if _ad_f > AI_DISAGREEMENT_THRESHOLD:
                level = _penalize_confidence(level)
    # v3 skill honesty: trailing walk-forward skill downgrades agreement-only
    # labels. All inputs None-safe (legacy callers pass nothing -> no-op).
    try:
        _sb = float(skill_brier) if skill_brier is not None and not isinstance(skill_brier, bool) else None
    except (TypeError, ValueError):
        _sb = None
    if _sb is not None and math.isfinite(_sb) and _sb >= SKILL_BRIER_THRESHOLD:
        level = _penalize_confidence(level)
    try:
        _se = float(skill_ece) if skill_ece is not None and not isinstance(skill_ece, bool) else None
    except (TypeError, ValueError):
        _se = None
    if _se is not None and math.isfinite(_se) and _se >= SKILL_ECE_THRESHOLD:
        level = _penalize_confidence(level)
    try:
        _ne = float(n_effective) if n_effective is not None and not isinstance(n_effective, bool) else None
    except (TypeError, ValueError):
        _ne = None
    if _ne is not None and math.isfinite(_ne) and _ne < SKILL_MIN_N_EFFECTIVE:
        level = _penalize_confidence(level)
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


@lru_cache(maxsize=512)
def _cached_target_date(as_of_iso: str, horizon_days: int, mic: str = "XNAS") -> str:
    """Memoized trading-day target (session-calendar lookups dominate)."""
    return _target_date(str(as_of_iso), int(horizon_days), str(mic))


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
        # Single-pass frame build (one loop over rows, not six).
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError(f"symbol must be a non-empty string, got {symbol!r}")
        bars = self.market.get_bars(symbol.strip().upper(), timeframe="1d", limit=BAR_LIMIT)
        rows = bars.get("bars", [])
        if len(rows) < 100:
            raise ValueError(f"insufficient history for {symbol!r}: {len(rows)} bars")
        # Drop incomplete OHLC rows (None open/high/low): a single None bar
        # must not kill a 500-bar forecast. Count drops in provenance.
        prov = dict(bars.get("provenance", {}) or {})
        try:
            kept = [r for r in rows if r.get("close") is not None]
            dropped_incomplete = len(rows) - len([r for r in kept if r.get("open") is not None and r.get("high") is not None and r.get("low") is not None])
            rows = [r for r in kept if r.get("open") is not None and r.get("high") is not None and r.get("low") is not None]
            if dropped_incomplete:
                mf = list(prov.get("missing_fields") or [])
                mf.append(f"dropped {dropped_incomplete} incomplete OHLC bars")
                prov["missing_fields"] = mf
                bars = {**bars, "provenance": prov}
            if len(rows) < 100:
                raise ValueError(f"insufficient history for {symbol!r}: {len(rows)} complete bars")
        except ValueError:
            raise
        except Exception:
            pass
        try:
            recs: list[tuple] = []
            for r in rows:
                if not isinstance(r, dict):
                    raise ValueError("malformed bar row")
                recs.append((
                    r["open"], r["high"], r["low"], r["close"],
                    float((r.get("volume")) or 0),
                    r.get("ts"),
                ))
            index = pd.to_datetime([t for _, _, _, _, _, t in recs])
            frame = pd.DataFrame(
                {
                    "open": [o for o, _, _, _, _, _ in recs],
                    "high": [h for _, h, _, _, _, _ in recs],
                    "low": [lo for _, _, lo, _, _, _ in recs],
                    "close": [c for _, _, _, c, _, _ in recs],
                    "volume": [v for _, _, _, _, v, _ in recs],
                },
                index=index,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"malformed bars for {symbol!r}: {exc}") from exc
        except Exception as exc:
            raise ValueError(f"malformed bars for {symbol!r}: {type(exc).__name__}") from exc
        return frame, bars

    def _resolve_versions(
        self, symbol: str, bars: dict, provenance: dict, stamp: str
    ) -> tuple[str, bool, bool, str]:
        base_data_version = _data_version({**provenance, "as_of": stamp})
        sse = _is_sse(symbol, bars)
        eux = _is_euronext(symbol, bars) and not sse
        if sse:
            data_version = f"{base_data_version}-sse"
        elif eux:
            data_version = f"{base_data_version}-eux"
        else:
            data_version = base_data_version
        return base_data_version, sse, eux, data_version

    # -- public ---------------------------------------------------------
    def forecast(
        self,
        symbol: str,
        horizon: int,
        as_of: str | None = None,
        member_brier: dict[str, float | None] | None = None,
        calibrator: dict | None = None,
        skill_brier: float | None = None,
        skill_ece: float | None = None,
    ) -> dict:
        """Forecast one symbol/horizon (ensemble-v3 direction + bands + risk).

        Optional v3 skill hooks (all backward-compatible no-ops when None):
          * ``member_brier``: trailing per-member Brier for inverse-Brier
            adaptive weighting (fixed ENSEMBLE_WEIGHTS fallback).
          * ``calibrator``: fitted isotonic/Platt dict for the raw mean
            (shrinkage fallback).
          * ``skill_brier``/``skill_ece``: trailing ensemble skill wired
            into the confidence label (agreement + skill honesty).
        """
        try:
            horizon = int(horizon)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ValueError(f"horizon must be one of {list(FORECAST_HORIZONS)}, got {horizon!r}") from exc
        if horizon not in FORECAST_HORIZONS:
            raise ValueError(f"horizon must be one of {list(FORECAST_HORIZONS)}, got {horizon}")
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError(f"symbol must be a non-empty string, got {symbol!r}")
        sym_upper = symbol.strip().upper()
        ohlcv, bars = self._load(symbol)
        provenance = dict(bars.get("provenance", {}))
        # Corporate-action lineage: bars are assumed split/dividend-adjusted
        # (ingest uses auto_adjust=True). An unadjusted 2:1 split looks like a
        # -50% single-day crash; an unadjusted 1:2 reverse split looks like a
        # +100% single-day jump. Flag that signature and cap confidence.
        # Thin liquidity (halts / illiquid names) costs one notch, not a cap.
        corp_cap, corp_notes = _corporate_action_assessment(ohlcv["close"])
        thin_note = _thin_liquidity_note(ohlcv)
        if corp_notes or thin_note:
            _mf = list(provenance.get("missing_fields") or [])
            _mf.extend(corp_notes)
            if thin_note:
                _mf.append(thin_note)
            provenance["missing_fields"] = _mf
        _crash = corp_cap
        stamp = as_of or str(provenance.get("as_of"))
        _, sse, eux, data_version = self._resolve_versions(
            symbol, bars, provenance, stamp)
        # Short-TTL cache: horizon tab switches + screener re-renders skip
        # the ensemble recompute (key includes data_version day stamp).
        if as_of is None:
            try:
                cached = _forecast_cache_get(sym_upper, horizon, data_version)
            except Exception:
                cached = None
            if isinstance(cached, dict) and cached.get("horizon_days") == horizon:
                return cached
        closes = ohlcv["close"]
        # Single-validate feature bundle: v1 frame (kept for compat) + v2
        # extended frame for the ML members share one validation + RSI pass.
        # ensemble-v3 fits logistic-v3 + gradient-boost-v1 on the EXTENDED
        # frame (14 cols); drift/momentum stay on closes/log-returns.
        features, ext_features = build_feature_bundle(ohlcv)

        # -- direction ensemble v3 (adaptive + isotonic-calibrated) -------
        # Members: drift + momentum (priors) + logistic-v3 + gradient-boost-v1
        # (ML, v2 features) + trend-persistence + mean-reversion (heuristics).
        # Fixed ENSEMBLE_WEIGHTS unless member_brier enables inverse-Brier
        # adaptive weights (renormalized over members that actually ran);
        # raw weighted mean -> isotonic/Platt (or shrinkage) -> clipped.
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
        try:
            logreg = LogisticDirectionModel(horizons=[horizon]).fit(ext_features, closes)
            logistic_p = float(
                logreg.predict_direction_proba(
                    ext_features.iloc[[-1]], as_of=stamp, data_version=data_version
                )[horizon].value
            )
            probas["logistic-direction"] = logistic_p
        except (ValueError, ImportError):
            pass  # single-class / too-few-rows / sklearn missing: skip member
        try:
            gb_model = GradientBoostDirectionModel(horizons=[horizon]).fit(ext_features, closes)
            gb_p = float(
                gb_model.predict_direction_proba(
                    ext_features.iloc[[-1]], as_of=stamp, data_version=data_version
                )[horizon].value
            )
            probas["gradient-boost-direction"] = gb_p
        except (ValueError, ImportError):
            pass  # same degrade path as logistic
        # 5th member: trend-persistence from past market data (63d trend +
        # drawdown + RSI persistence). Reuses the v2 frame from the bundle
        # above (no rebuild; identical values).
        try:
            trend_p = _trend_persistence_signal(ohlcv, _ext=ext_features)
            if trend_p is not None and 0.0 <= float(trend_p) <= 1.0:
                probas["trend-persistence"] = float(trend_p)
        except Exception:
            pass
        # 6th member (v3): mean-reversion contrarian (RSI + 5d reversal).
        # Diversifies the trend-following members; same frame, no rebuild.
        try:
            mr_p = _mean_reversion_signal(ohlcv, _ext=ext_features)
            if mr_p is not None and 0.0 <= float(mr_p) <= 1.0:
                probas["mean-reversion"] = float(mr_p)
        except Exception:
            pass
        us_raw, us_weights, us_spread, _us_std = _weighted_mean(
            probas, member_brier=member_brier
        )
        us_direction_raw = float(us_raw)
        us_direction = float(_calibrate_prob(us_raw, calibrator=calibrator))
        formulas: dict[str, str] = {
            name: MEMBER_FORMULAS[name] for name in probas if name in MEMBER_FORMULAS
        }

        # -- return range / regime / drawdown ----------------------------
        band = return_quantiles(
            closes, horizons=[horizon], as_of=stamp, data_version=data_version
        )[horizon].value
        regime_res = volatility_regime(closes, as_of=stamp, data_version=data_version)
        dd_res = drawdown_probability(
            closes, horizon, as_of=stamp, data_version=data_version
        )
        try:
            _n_eff = float(band.get("n_effective", float(band.get("n_windows", 0)) / max(horizon, 1)))
        except Exception:
            _n_eff = None
        try:
            _width = float(band["high"]) - float(band["low"])
        except Exception:
            _width = None
        us_range = {
            "low": float(band["low"]),
            "mid": float(band["median"]),
            "high": float(band["high"]),
            "lower_q": float(band["lower_q"]),
            "upper_q": float(band["upper_q"]),
            "n_windows": int(band["n_windows"]),
            "n_effective": _n_eff,
            "width": _width,
            "coverage": f"80% empirical (q{float(band['lower_q']):.2g}/q{float(band['upper_q']):.2g})",
        }
        regime = str(regime_res.value["regime"])
        try:
            regime_detail = dict(regime_res.value)
        except Exception:
            regime_detail = {"regime": regime}
        try:
            dd_detail = dict(dd_res.value)
        except Exception:
            dd_detail = {}
        dd_prob = float(dd_res.value["probability"])

        if sse:
            # -- SSE path: blend US ensemble 50/50 with SSE drift ---------
            # sse-drift sees the same trailing log-returns (winsorized
            # internally to the +/-10% limit band) with wider tails.
            # Blend on RAW means then recalibrate (consistent with US path).
            sse_model = SseDriftBaseline().fit(lret)
            sse_p = float(
                sse_model.direction_probability(
                    horizon, as_of=stamp, data_version=data_version
                ).value
            )
            probas["sse-drift"] = sse_p
            formulas["sse-drift"] = (
                "P(up_h) = Phi(mu*h/(sigma*sqrt(h))); winsorized SSE drift"
            )
            direction_raw = float((us_direction_raw + sse_p) / 2.0)
            direction = float(_calibrate_prob(direction_raw, calibrator=calibrator))
            spread = float(max(probas.values()) - min(probas.values())) if probas else 0.0
            try:
                _sse_std = float(pd.Series(list(probas.values())).std(ddof=1))
                if not math.isfinite(_sse_std):
                    _sse_std = 0.0
            except Exception:
                _sse_std = 0.0
            # Union of ranges: conservative envelope of the US empirical
            # band and the wider SSE drift band; mid is the mean of mids.
            sse_band = sse_model.expected_return_range(
                horizon, as_of=stamp, data_version=data_version
            ).value
            _er_low = float(min(us_range["low"], sse_band["low"]))
            _er_high = float(max(us_range["high"], sse_band["high"]))
            expected_range = {
                "low": _er_low,
                "mid": float((us_range["mid"] + float(sse_band["mid"])) / 2.0),
                "high": _er_high,
                "lower_q": float(us_range["lower_q"]),
                "upper_q": float(us_range["upper_q"]),
                "n_windows": int(us_range["n_windows"]),
                "n_effective": us_range.get("n_effective"),
                "width": float(_er_high - _er_low),
                "coverage": str(us_range.get("coverage") or "80% empirical"),
            }
            try:
                sse_features = build_sse_features(ohlcv)
                proximity_fired = limit_proximity_triggered(sse_features)
            except ValueError:
                proximity_fired = False
            base_confidence = _confidence(
                spread,
                len(probas),
                str(provenance.get("quality_grade") or "U"),
                regime,
                dd_prob,
                direction_prob=direction,
                skill_brier=skill_brier,
                skill_ece=skill_ece,
                n_effective=us_range.get("n_effective"),
            )
            confidence = (
                _penalize_confidence(base_confidence)
                if proximity_fired
                else base_confidence
            )
            model_version = SSE_BLEND_VERSION
            feature_version = SSE_FEATURE_VERSION
            weights_used = {**{k: v * 0.5 for k, v in us_weights.items()}, "sse-drift": 0.5}
            ensemble_std = float(_sse_std)
        elif eux:
            # -- Euronext path: blend US ensemble 50/50 with eux drift --
            # eux-drift sees the same trailing log-returns (winsorized
            # internally to the +/-15% robustness cap; Euronext has no
            # hard daily limits) with a z=1.15 band. Blend on RAW then
            # recalibrate, mirroring the SSE path.
            eux_model = EuxDriftBaseline().fit(lret)
            eux_p = float(
                eux_model.direction_probability(
                    horizon, as_of=stamp, data_version=data_version
                ).value
            )
            probas["eux-drift"] = eux_p
            formulas["eux-drift"] = (
                "P(up_h) = Phi(mu*h/(sigma*sqrt(h))); winsorized Euronext drift"
            )
            direction_raw = float((us_direction_raw + eux_p) / 2.0)
            direction = float(_calibrate_prob(direction_raw, calibrator=calibrator))
            spread = float(max(probas.values()) - min(probas.values())) if probas else 0.0
            try:
                _eux_std = float(pd.Series(list(probas.values())).std(ddof=1))
                if not math.isfinite(_eux_std):
                    _eux_std = 0.0
            except Exception:
                _eux_std = 0.0
            # Union of ranges: conservative envelope of the US empirical
            # band and the eux drift band; mid is the mean of mids.
            eux_band = eux_model.expected_return_range(
                horizon, as_of=stamp, data_version=data_version
            ).value
            _er_low = float(min(us_range["low"], eux_band["low"]))
            _er_high = float(max(us_range["high"], eux_band["high"]))
            expected_range = {
                "low": _er_low,
                "mid": float((us_range["mid"] + float(eux_band["mid"])) / 2.0),
                "high": _er_high,
                "lower_q": float(us_range["lower_q"]),
                "upper_q": float(us_range["upper_q"]),
                "n_windows": int(us_range["n_windows"]),
                "n_effective": us_range.get("n_effective"),
                "width": float(_er_high - _er_low),
                "coverage": str(us_range.get("coverage") or "80% empirical"),
            }
            # NOTE: the EUX feature frame is version-stamped via
            # EUX_FEATURE_VERSION above; its values are not consumed by the
            # drift blend, so no second feature build is needed here.
            confidence = _confidence(
                spread,
                len(probas),
                str(provenance.get("quality_grade") or "U"),
                regime,
                dd_prob,
                direction_prob=direction,
                skill_brier=skill_brier,
                skill_ece=skill_ece,
                n_effective=us_range.get("n_effective"),
            )
            model_version = EUX_BLEND_VERSION
            feature_version = EUX_FEATURE_VERSION
            weights_used = {**{k: v * 0.5 for k, v in us_weights.items()}, "eux-drift": 0.5}
            ensemble_std = float(_eux_std)
        else:
            direction_raw = float(us_direction_raw)
            direction = us_direction
            spread = us_spread
            ensemble_std = float(_us_std)
            weights_used = dict(us_weights)
            expected_range = us_range
            confidence = _confidence(
                spread,
                len(probas),
                str(provenance.get("quality_grade") or "U"),
                regime,
                dd_prob,
                direction_prob=direction,
                skill_brier=skill_brier,
                skill_ece=skill_ece,
                n_effective=us_range.get("n_effective"),
            )
            model_version = ENSEMBLE_VERSION
            feature_version = EXTENDED_FEATURE_VERSION
        # Thin-liquidity honesty: non-trading stretches cost one notch (the
        # reason is already disclosed in provenance missing_fields above).
        confidence_reasons: list[str] = []
        # Base penalties already inside _confidence: regime, drawdown, grade,
        # sharpness, skill. Record them for the wire `confidence_reasons`.
        try:
            if str(regime).lower() in VOLATILITY_PENALTY_REGIMES:
                confidence_reasons.append(f"volatility regime {regime} penalty (-1 notch)")
        except Exception:
            pass
        try:
            if isinstance(dd_prob, (int, float)) and not isinstance(dd_prob, bool):
                if math.isfinite(float(dd_prob)) and float(dd_prob) >= DRAWDOWN_PENALTY_THRESHOLD:
                    confidence_reasons.append(
                        f"drawdown {float(dd_prob):.0%} >= {DRAWDOWN_PENALTY_THRESHOLD:.0%} penalty"
                    )
                elif isinstance(dd_prob, float) and math.isnan(float(dd_prob)):
                    confidence_reasons.append("drawdown unknown penalty (conservative)")
        except Exception:
            pass
        # v3 skill honesty reasons (mirror the _confidence gates above).
        try:
            if skill_brier is not None and not isinstance(skill_brier, bool):
                _sbf = float(skill_brier)
                if math.isfinite(_sbf) and _sbf >= SKILL_BRIER_THRESHOLD:
                    confidence_reasons.append(
                        f"trailing Brier {_sbf:.3f} >= {SKILL_BRIER_THRESHOLD:.2f} (coin-flip) penalty"
                    )
        except Exception:
            pass
        try:
            if skill_ece is not None and not isinstance(skill_ece, bool):
                _sef = float(skill_ece)
                if math.isfinite(_sef) and _sef >= SKILL_ECE_THRESHOLD:
                    confidence_reasons.append(
                        f"trailing ECE {_sef:.3f} >= {SKILL_ECE_THRESHOLD:.2f} penalty"
                    )
        except Exception:
            pass
        try:
            _neff = expected_range.get("n_effective")
            if _neff is not None and not isinstance(_neff, bool):
                _neff_f = float(_neff)
                if math.isfinite(_neff_f) and _neff_f < SKILL_MIN_N_EFFECTIVE:
                    confidence_reasons.append(
                        f"thin effective sample (n_effective {_neff_f:.1f} < {SKILL_MIN_N_EFFECTIVE:.0f}) penalty"
                    )
        except Exception:
            pass
        if thin_note:
            try:
                confidence = _penalize_confidence(confidence)
                confidence_reasons.append(f"thin liquidity: {thin_note}")
            except Exception:
                pass
        # Honesty cap: stub/stale (fallback or grade C) never serves "high".
        try:
            _before_stub = confidence
            confidence = _cap_stub_confidence(confidence, provenance)
            if confidence != _before_stub:
                confidence_reasons.append("stub/fallback grade cap (high->moderate)")
        except Exception:
            pass

        # Staleness + fallback honesty: stub/fallback bars or months-old data
        # must never read as high-confidence. Cap to low and let the
        # limitations string disclose it (see api/forecast.forecast_limitations).
        try:
            _fallback = bool(provenance.get("fallback_used"))
        except Exception:
            _fallback = False
        _stale = False
        try:
            _asof_raw = str(provenance.get("as_of") or stamp or "")
            _asof_dt = datetime.fromisoformat(_asof_raw.replace("Z", "+00:00"))
            if _asof_dt.tzinfo is None:
                _asof_dt = _asof_dt.replace(tzinfo=timezone.utc)
            _age_days = (datetime.now(timezone.utc) - _asof_dt).total_seconds() / 86400.0
            _stale = _age_days > 7.0
        except Exception:
            _stale = False
        if _fallback or _stale or _crash:
            if confidence != "low":
                confidence_reasons.append(
                    "fallback/stale/corporate-action cap (confidence=low)"
                )
            confidence = "low"
        try:
            if spread > CONFIDENCE_MODERATE_MAX_SPREAD:
                confidence_reasons.append(
                    f"member disagreement high (spread {spread:.2f})"
                )
        except Exception:
            pass
        try:
            _n_penalties = len(confidence_reasons)
        except Exception:
            _n_penalties = 0
        try:
            confidence_score = _confidence_score(spread, _n_penalties)
        except Exception:
            confidence_score = 0.0

        # -- JSON safety: non-finite floats are invalid JSON (NaN/inf) ----
        # Finite inputs pass through bit-identical; only pathological model
        # outputs (e.g. exp() overflow on extreme synthetic drift) map to
        # None instead of leaking NaN/inf to the wire/DB.
        _clean_direction = _finite_or_none(direction)
        if _clean_direction is None:
            raise ValueError("non-finite direction probability")
        direction = _clean_direction
        try:
            direction_raw = float(_finite_or_none(direction_raw))  # type: ignore[name-defined]
        except Exception:
            direction_raw = float(direction)
        if direction_raw is None:  # type: ignore[unreachable]
            direction_raw = float(direction)
        dd_prob = _finite_or_none(dd_prob)
        for _bound in ("low", "mid", "high"):
            expected_range[_bound] = _finite_or_none(expected_range.get(_bound))
        try:
            expected_range["width"] = _finite_or_none(expected_range.get("width"))
        except Exception:
            pass
        try:
            _ne = expected_range.get("n_effective")
            expected_range["n_effective"] = float(_ne) if _ne is not None else None
            if expected_range["n_effective"] is not None and not math.isfinite(
                float(expected_range["n_effective"])
            ):
                expected_range["n_effective"] = None
        except Exception:
            expected_range["n_effective"] = None
        for _name, _value in list(probas.items()):
            _clean_member = _finite_or_none(_value)
            # JSON safety: non-finite members sanitize to None (never NaN/inf).
            # Reachable only on pathological model output; direction already
            # validated finite above, so this never changes valid ensembles.
            probas[_name] = _clean_member
        try:
            weights_used = {k: float(v) for k, v in weights_used.items()}  # type: ignore[name-defined]
        except Exception:
            weights_used = {}  # type: ignore[no-redef]
        try:
            ensemble_std = float(ensemble_std)  # type: ignore[name-defined]
            if not math.isfinite(ensemble_std):
                ensemble_std = 0.0
        except Exception:
            ensemble_std = 0.0
        # Evidence = members that actually ran (a failed/skipped fit is never
        # listed: its version would otherwise render as contributing evidence).
        model_members = [
            MEMBER_VERSIONS[name]
            for name in sorted(probas)
            if probas[name] is not None and name in MEMBER_VERSIONS
        ]

        instrument_id = bars.get("instrument_id") or f"stub-{symbol.strip().upper()}"
        # Anchor target on the LAST BAR timestamp (market time), not fetch
        # wall-clock: if cron stalls, the target must not slide forward on
        # stale bars. Memoized: session-calendar lookups dominate cost.
        try:
            _last_ts = ohlcv.index.max()
            _anchor = _last_ts.isoformat() if _last_ts is not None else stamp
        except Exception:
            _anchor = stamp
        target_date = _cached_target_date(
            _anchor, horizon, _target_mic(symbol, bars, sse, eux)
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

        # Target-price range: last close scaled by the return band (helps the
        # UI render price levels without refetching quotes). Deterministic.
        try:
            _last_close = float(ohlcv["close"].iloc[-1])
        except Exception:
            _last_close = None
        try:
            if _last_close is not None and math.isfinite(_last_close) and _last_close > 0:
                target_price = {
                    "last_close": float(_last_close),
                    "low": float(_last_close * (1.0 + float(expected_range["low"])))
                    if expected_range["low"] is not None else None,
                    "mid": float(_last_close * (1.0 + float(expected_range["mid"])))
                    if expected_range.get("mid") is not None else None,
                    "high": float(_last_close * (1.0 + float(expected_range["high"])))
                    if expected_range["high"] is not None else None,
                }
            else:
                target_price = None
        except Exception:
            target_price = None

        # NOTE (future per-user calibration hook): per-user probability
        # shaping belongs here as a pure function of (payload, user_tier) —
        # owned by another agent (auth/tiers). Never branch global
        # determinism on identity here; identical inputs stay identical.
        try:
            _adaptive = bool(
                isinstance(member_brier, dict) and len(member_brier) >= 2
                and set(weights_used) <= set(member_brier)
                and any(
                    abs(float(weights_used.get(k, 0.0)) - float(ENSEMBLE_WEIGHTS.get(k, 0.0) or 0.0)) > 1e-9
                    for k in weights_used if k in ENSEMBLE_WEIGHTS
                )
            )
        except Exception:
            _adaptive = False
        try:
            _cal_method = "isotonic-platt" if isinstance(calibrator, dict) and calibrator else "shrinkage-0.8"
        except Exception:
            _cal_method = "shrinkage-0.8"
        payload = {
            "symbol": symbol.strip().upper(),
            "horizon_days": horizon,
            "direction_probability": direction,
            "direction_probability_raw": float(direction_raw),
            "calibration_method": _cal_method,
            "adaptive_weights": bool(_adaptive),
            "skill_brier": _finite_or_none(skill_brier),
            "skill_ece": _finite_or_none(skill_ece),
            "expected_return_range": expected_range,
            "volatility_regime": regime,
            "volatility_detail": regime_detail,
            "drawdown_probability": dd_prob,
            "drawdown_detail": dd_detail,
            "confidence": confidence,
            "confidence_score": float(confidence_score),
            "confidence_reasons": list(confidence_reasons),
            "ensemble_weights": dict(weights_used),
            "ensemble_spread": float(spread),
            "ensemble_std": float(ensemble_std),
            "n_members": int(len([v for v in probas.values() if v is not None])),
            "target_price": target_price,
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
        if as_of is None:
            try:
                _forecast_cache_set(sym_upper, horizon, data_version, payload)
            except Exception:
                pass
        return payload

    def forecast_all(
        self,
        symbol: str,
        as_of: str | None = None,
        skill_by_horizon: dict[int, dict] | None = None,
    ) -> dict[int, dict]:
        """All horizons with ONE bars load + ONE feature build.

        Shared: bars, OHLCV frame, v1+v2 features, log-returns, quantile bands
        (single multi-horizon call), volatility regime (horizon-independent),
        trend-persistence + mean-reversion signals and the multi-horizon ML
        fits (logistic-v3 + gradient-boost-v1 on the extended frame). Per
        horizon only the cheap drift/momentum scalars, venue-drift blend,
        drawdown probability and record assembly remain. ensemble-v3
        adaptive-weighted + isotonic-calibrated (skill_by_horizon[h] may
        carry {member_brier, calibrator, skill_brier, skill_ece}; missing
        horizons fall back to fixed weights + shrinkage).
        """
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError(f"symbol must be a non-empty string, got {symbol!r}")
        sym_upper = symbol.strip().upper()
        ohlcv, bars = self._load(symbol)
        provenance = dict(bars.get("provenance", {}))
        # Corporate-action lineage (mirrors forecast()): unadjusted splits
        # look like -45% single-day crashes, reverse splits like +90% jumps;
        # flag + cap confidence. Thin liquidity costs one notch per horizon.
        corp_cap, corp_notes = _corporate_action_assessment(ohlcv["close"])
        thin_note = _thin_liquidity_note(ohlcv)
        if corp_notes or thin_note:
            _mf = list(provenance.get("missing_fields") or [])
            _mf.extend(corp_notes)
            if thin_note:
                _mf.append(thin_note)
            provenance["missing_fields"] = _mf
        _crash = corp_cap
        stamp = as_of or str(provenance.get("as_of"))
        _, sse, eux, data_version = self._resolve_versions(symbol, bars, provenance, stamp)
        # Cache fast path: all three horizons warm -> return without recompute.
        if as_of is None:
            try:
                warm = {
                    h: _forecast_cache_get(sym_upper, int(h), data_version)
                    for h in FORECAST_HORIZONS
                }
                if all(isinstance(v, dict) for v in warm.values()):
                    return {int(h): v for h, v in warm.items()}  # type: ignore[misc]
            except Exception:
                pass
        closes = ohlcv["close"]
        # ONE bars load + ONE feature build shared by all horizons (bundle
        # shares validation + RSI across v1/v2). Venue features, regime,
        # staleness and the venue MIC are horizon-independent: hoist them.
        features, ext_features = build_feature_bundle(ohlcv)
        lret = log_returns(closes).dropna()
        mic = _target_mic(symbol, bars, sse, eux)
        try:
            _fallback_all = bool(provenance.get("fallback_used"))
        except Exception:
            _fallback_all = False
        _stale_all = False
        try:
            _asof_dt = datetime.fromisoformat(
                str(provenance.get("as_of") or stamp or "").replace("Z", "+00:00"))
            if _asof_dt.tzinfo is None:
                _asof_dt = _asof_dt.replace(tzinfo=timezone.utc)
            _stale_all = (
                datetime.now(timezone.utc) - _asof_dt).total_seconds() / 86400.0 > 7.0
        except Exception:
            _stale_all = False
        if sse:
            try:
                sse_features_all = build_sse_features(ohlcv)
                proximity_all = limit_proximity_triggered(sse_features_all)
            except ValueError:
                proximity_all = False
        else:
            proximity_all = False
        try:
            bands_all = return_quantiles(
                closes, horizons=list(FORECAST_HORIZONS),
                as_of=stamp, data_version=data_version,
            )
        except Exception:
            bands_all = {}
        try:
            regime_res = volatility_regime(closes, as_of=stamp, data_version=data_version)
            regime = str(regime_res.value["regime"])
            try:
                regime_detail_all = dict(regime_res.value)
            except Exception:
                regime_detail_all = {"regime": regime}
        except Exception:
            regime_res, regime, regime_detail_all = None, "normal", {"regime": "normal"}
        try:
            trend_p = _trend_persistence_signal(ohlcv, _ext=ext_features)
            if trend_p is not None and not 0.0 <= float(trend_p) <= 1.0:
                trend_p = None
        except Exception:
            trend_p = None
        try:
            mr_p_all = _mean_reversion_signal(ohlcv, _ext=ext_features)
            if mr_p_all is not None and not 0.0 <= float(mr_p_all) <= 1.0:
                mr_p_all = None
        except Exception:
            mr_p_all = None
        try:
            logreg_all = LogisticDirectionModel(
                horizons=list(FORECAST_HORIZONS)
            ).fit(ext_features, closes)
        except (ValueError, ImportError):
            logreg_all = None
        try:
            gb_all = GradientBoostDirectionModel(
                horizons=list(FORECAST_HORIZONS)
            ).fit(ext_features, closes)
        except (ValueError, ImportError):
            gb_all = None
        try:
            drift_model = HistoricalDriftBaseline().fit(lret)
        except ValueError:
            drift_model = None
        try:
            momentum_model = MomentumBaseline().fit(closes)
        except ValueError:
            momentum_model = None
        # Venue drift fits are horizon-independent (same trailing log-returns):
        # fit once, predict per horizon. Identical values to per-horizon fits;
        # a bad lret raises here exactly as the first loop iteration would.
        sse_model_all = SseDriftBaseline().fit(lret) if sse else None
        eux_model_all = EuxDriftBaseline().fit(lret) if eux else None
        try:
            _last_close_all = float(ohlcv["close"].iloc[-1])
            if not math.isfinite(_last_close_all) or _last_close_all <= 0:
                _last_close_all = None
        except Exception:
            _last_close_all = None
        out: dict[int, dict] = {}
        for horizon in FORECAST_HORIZONS:
            horizon = int(horizon)
            probas: dict[str, float] = {}
            formulas_loop: dict[str, str] = {}
            if drift_model is not None:
                try:
                    probas["historical-drift"] = float(
                        drift_model.direction_probability(
                            horizon, as_of=stamp, data_version=data_version
                        ).value
                    )
                except ValueError:
                    pass
            if momentum_model is not None:
                try:
                    probas["momentum"] = float(
                        momentum_model.direction_probability(
                            horizon, as_of=stamp, data_version=data_version
                        ).value
                    )
                except ValueError:
                    pass
            if logreg_all is not None:
                try:
                    probas["logistic-direction"] = float(
                        logreg_all.predict_direction_proba(
                            ext_features.iloc[[-1]], as_of=stamp,
                            data_version=data_version,
                        )[horizon].value
                    )
                except (ValueError, IndexError, KeyError):
                    pass
            if gb_all is not None:
                try:
                    probas["gradient-boost-direction"] = float(
                        gb_all.predict_direction_proba(
                            ext_features.iloc[[-1]], as_of=stamp,
                            data_version=data_version,
                        )[horizon].value
                    )
                except (ValueError, IndexError, KeyError):
                    pass
            if trend_p is not None:
                probas["trend-persistence"] = float(trend_p)
            if mr_p_all is not None:
                probas["mean-reversion"] = float(mr_p_all)
            for _m in list(probas):
                if _m in MEMBER_FORMULAS:
                    formulas_loop[_m] = MEMBER_FORMULAS[_m]
            try:
                _skill_h = (skill_by_horizon or {}).get(int(horizon)) or {}
            except Exception:
                _skill_h = {}
            _mb_h = _skill_h.get("member_brier") if isinstance(_skill_h, dict) else None
            _cal_h = _skill_h.get("calibrator") if isinstance(_skill_h, dict) else None
            us_raw_loop, us_w_loop, us_spread_loop, us_std_loop = _weighted_mean(
                probas, member_brier=_mb_h
            )
            us_direction_raw_loop = float(us_raw_loop)
            us_direction_loop = float(_calibrate_prob(us_raw_loop, calibrator=_cal_h))
            band_obj = bands_all.get(horizon) if isinstance(bands_all, dict) else None
            try:
                band = band_obj.value if band_obj is not None else None
                try:
                    _ne = float(band.get("n_effective", float(band.get("n_windows", 0)) / max(horizon, 1)))
                except Exception:
                    _ne = None
                try:
                    _w = float(band["high"]) - float(band["low"])
                except Exception:
                    _w = None
                us_range = {
                    "low": float(band["low"]),
                    "mid": float(band["median"]),
                    "high": float(band["high"]),
                    "lower_q": float(band["lower_q"]),
                    "upper_q": float(band["upper_q"]),
                    "n_windows": int(band["n_windows"]),
                    "n_effective": _ne,
                    "width": _w,
                    "coverage": f"80% empirical (q{float(band['lower_q']):.2g}/q{float(band['upper_q']):.2g})",
                }
            except Exception:
                # Fallback: single-horizon band (mirrors forecast()).
                single = return_quantiles(
                    closes, horizons=[horizon], as_of=stamp,
                    data_version=data_version,
                )[horizon].value
                try:
                    _ne2 = float(single.get("n_effective", float(single.get("n_windows", 0)) / max(horizon, 1)))
                except Exception:
                    _ne2 = None
                us_range = {
                    "low": float(single["low"]),
                    "mid": float(single["median"]),
                    "high": float(single["high"]),
                    "lower_q": float(single["lower_q"]),
                    "upper_q": float(single["upper_q"]),
                    "n_windows": int(single["n_windows"]),
                    "n_effective": _ne2,
                    "width": float(single["high"]) - float(single["low"]),
                    "coverage": "80% empirical",
                }
            try:
                dd_prob = float(
                    drawdown_probability(
                        closes, horizon, as_of=stamp, data_version=data_version
                    ).value["probability"]
                )
            except Exception:
                dd_prob = float("nan")
            try:
                dd_detail_loop = dict(
                    drawdown_probability(
                        closes, horizon, as_of=stamp, data_version=data_version
                    ).value
                )
            except Exception:
                dd_detail_loop = {}
            if sse:
                sse_p = float(
                    sse_model_all.direction_probability(
                        horizon, as_of=stamp, data_version=data_version
                    ).value
                )
                probas["sse-drift"] = sse_p
                formulas_loop["sse-drift"] = "P(up_h) = Phi(mu*h/(sigma*sqrt(h))); winsorized SSE drift"
                direction_raw_loop = float((us_direction_raw_loop + sse_p) / 2.0)
                direction = float(_calibrate_prob(direction_raw_loop, calibrator=_cal_h))
                sse_band = sse_model_all.expected_return_range(
                    horizon, as_of=stamp, data_version=data_version
                ).value
                _er_low = float(min(us_range["low"], sse_band["low"]))
                _er_high = float(max(us_range["high"], sse_band["high"]))
                expected_range = {
                    "low": _er_low,
                    "mid": float((us_range["mid"] + float(sse_band["mid"])) / 2.0),
                    "high": _er_high,
                    "lower_q": float(us_range["lower_q"]),
                    "upper_q": float(us_range["upper_q"]),
                    "n_windows": int(us_range["n_windows"]),
                    "n_effective": us_range.get("n_effective"),
                    "width": float(_er_high - _er_low),
                    "coverage": str(us_range.get("coverage") or "80% empirical"),
                }
                # Limit proximity is horizon-independent (same trailing bars):
                # hoisted above the loop (identical values, one build).
                proximity_fired = proximity_all
                _sb_h = _skill_h.get("skill_brier") if isinstance(_skill_h, dict) else None
                _se_h = _skill_h.get("skill_ece") if isinstance(_skill_h, dict) else None
                base_conf = _confidence(
                    float(max(probas.values()) - min(probas.values())) if probas else 0.0,
                    len(probas), str(provenance.get("quality_grade") or "U"),
                    regime, dd_prob,
                    direction_prob=direction,
                    skill_brier=_sb_h,
                    skill_ece=_se_h,
                    n_effective=us_range.get("n_effective"),
                )
                confidence = _penalize_confidence(base_conf) if proximity_fired else base_conf
                model_version, feature_version = SSE_BLEND_VERSION, SSE_FEATURE_VERSION
                weights_loop = {**{k: v * 0.5 for k, v in us_w_loop.items()}, "sse-drift": 0.5}
                try:
                    std_loop = float(pd.Series(list(probas.values())).std(ddof=1))
                    if not math.isfinite(std_loop):
                        std_loop = 0.0
                except Exception:
                    std_loop = 0.0
                spread_loop = float(max(probas.values()) - min(probas.values())) if probas else 0.0
            elif eux:
                eux_p = float(
                    eux_model_all.direction_probability(
                        horizon, as_of=stamp, data_version=data_version
                    ).value
                )
                probas["eux-drift"] = eux_p
                formulas_loop["eux-drift"] = "P(up_h) = Phi(mu*h/(sigma*sqrt(h))); winsorized Euronext drift"
                direction_raw_loop = float((us_direction_raw_loop + eux_p) / 2.0)
                direction = float(_calibrate_prob(direction_raw_loop, calibrator=_cal_h))
                eux_band = eux_model_all.expected_return_range(
                    horizon, as_of=stamp, data_version=data_version
                ).value
                _er_low = float(min(us_range["low"], eux_band["low"]))
                _er_high = float(max(us_range["high"], eux_band["high"]))
                expected_range = {
                    "low": _er_low,
                    "mid": float((us_range["mid"] + float(eux_band["mid"])) / 2.0),
                    "high": _er_high,
                    "lower_q": float(us_range["lower_q"]),
                    "upper_q": float(us_range["upper_q"]),
                    "n_windows": int(us_range["n_windows"]),
                    "n_effective": us_range.get("n_effective"),
                    "width": float(_er_high - _er_low),
                    "coverage": str(us_range.get("coverage") or "80% empirical"),
                }
                confidence = _confidence(
                    float(max(probas.values()) - min(probas.values())) if probas else 0.0,
                    len(probas), str(provenance.get("quality_grade") or "U"),
                    regime, dd_prob,
                    direction_prob=direction,
                    skill_brier=_skill_h.get("skill_brier") if isinstance(_skill_h, dict) else None,
                    skill_ece=_skill_h.get("skill_ece") if isinstance(_skill_h, dict) else None,
                    n_effective=us_range.get("n_effective"),
                )
                model_version, feature_version = EUX_BLEND_VERSION, EUX_FEATURE_VERSION
                weights_loop = {**{k: v * 0.5 for k, v in us_w_loop.items()}, "eux-drift": 0.5}
                try:
                    std_loop = float(pd.Series(list(probas.values())).std(ddof=1))
                    if not math.isfinite(std_loop):
                        std_loop = 0.0
                except Exception:
                    std_loop = 0.0
                spread_loop = float(max(probas.values()) - min(probas.values())) if probas else 0.0
            else:
                direction_raw_loop = float(us_direction_raw_loop)
                direction = float(us_direction_loop)
                expected_range = us_range
                confidence = _confidence(
                    float(us_spread_loop),
                    len(probas), str(provenance.get("quality_grade") or "U"),
                    regime, dd_prob,
                    direction_prob=direction,
                    skill_brier=_skill_h.get("skill_brier") if isinstance(_skill_h, dict) else None,
                    skill_ece=_skill_h.get("skill_ece") if isinstance(_skill_h, dict) else None,
                    n_effective=us_range.get("n_effective"),
                )
                model_version, feature_version = ENSEMBLE_VERSION, EXTENDED_FEATURE_VERSION
                weights_loop = dict(us_w_loop)
                std_loop = float(us_std_loop)
                spread_loop = float(us_spread_loop)
            confidence_reasons_loop: list[str] = []
            try:
                if str(regime).lower() in VOLATILITY_PENALTY_REGIMES:
                    confidence_reasons_loop.append(f"volatility regime {regime} penalty (-1 notch)")
            except Exception:
                pass
            try:
                if isinstance(dd_prob, (int, float)) and not isinstance(dd_prob, bool):
                    if math.isfinite(float(dd_prob)) and float(dd_prob) >= DRAWDOWN_PENALTY_THRESHOLD:
                        confidence_reasons_loop.append(
                            f"drawdown {float(dd_prob):.0%} >= {DRAWDOWN_PENALTY_THRESHOLD:.0%} penalty"
                        )
            except Exception:
                pass
            try:
                confidence = _cap_stub_confidence(confidence, provenance)
            except Exception:
                pass
            # Thin-liquidity honesty (mirrors forecast()): one notch, floored.
            if thin_note:
                try:
                    confidence = _penalize_confidence(confidence)
                    confidence_reasons_loop.append(f"thin liquidity: {thin_note}")
                except Exception:
                    pass
            # Staleness + fallback + crash honesty (mirrors forecast()):
            # stub/fallback bars, >7d-old data, or a crash/jump signature cap
            # confidence at low (hoisted: provenance/stamp are loop-fixed).
            if _fallback_all or _stale_all or _crash:
                if confidence != "low":
                    confidence_reasons_loop.append("fallback/stale/corporate-action cap (confidence=low)")
                confidence = "low"
            try:
                if spread_loop > CONFIDENCE_MODERATE_MAX_SPREAD:
                    confidence_reasons_loop.append(
                        f"member disagreement high (spread {spread_loop:.2f})"
                    )
            except Exception:
                pass
            try:
                confidence_score_loop = _confidence_score(spread_loop, len(confidence_reasons_loop))
            except Exception:
                confidence_score_loop = 0.0
            clean_direction = _finite_or_none(direction)
            if clean_direction is None:
                raise ValueError("non-finite direction probability")
            direction = clean_direction
            try:
                direction_raw_loop = float(direction_raw_loop)
                if not math.isfinite(direction_raw_loop):
                    direction_raw_loop = float(direction)
            except Exception:
                direction_raw_loop = float(direction)
            dd_prob = _finite_or_none(dd_prob)
            for bound in ("low", "mid", "high"):
                expected_range[bound] = _finite_or_none(expected_range.get(bound))
            try:
                expected_range["width"] = _finite_or_none(expected_range.get("width"))
            except Exception:
                pass
            for name, value in list(probas.items()):
                probas[name] = _finite_or_none(value)
            try:
                weights_loop = {k: float(v) for k, v in weights_loop.items()}
            except Exception:
                weights_loop = {}
            model_members = [
                MEMBER_VERSIONS[name]
                for name in sorted(probas)
                if probas[name] is not None and name in MEMBER_VERSIONS
            ]
            instrument_id = bars.get("instrument_id") or f"stub-{sym_upper}"
            target_date = _cached_target_date(stamp, horizon, mic)
            try:
                if _last_close_all is not None:
                    target_price_loop = {
                        "last_close": float(_last_close_all),
                        "low": float(_last_close_all * (1.0 + float(expected_range["low"])))
                        if expected_range["low"] is not None else None,
                        "mid": float(_last_close_all * (1.0 + float(expected_range.get("mid", 0) or 0)))
                        if expected_range.get("mid") is not None else None,
                        "high": float(_last_close_all * (1.0 + float(expected_range["high"])))
                        if expected_range["high"] is not None else None,
                    }
                else:
                    target_price_loop = None
            except Exception:
                target_price_loop = None
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
            payload = {
                "symbol": sym_upper,
                "horizon_days": horizon,
                "direction_probability": direction,
                "direction_probability_raw": float(direction_raw_loop),
                "expected_return_range": expected_range,
                "volatility_regime": regime,
                "volatility_detail": dict(regime_detail_all),
                "drawdown_probability": dd_prob,
                "drawdown_detail": dict(dd_detail_loop),
                "confidence": confidence,
                "confidence_score": float(confidence_score_loop),
                "confidence_reasons": list(confidence_reasons_loop),
                "ensemble_weights": dict(weights_loop),
                "ensemble_spread": float(spread_loop),
                "ensemble_std": float(std_loop),
                "n_members": int(len([v for v in probas.values() if v is not None])),
                "target_price": target_price_loop,
                "model_version": model_version,
                "model_members": model_members,
                "components": dict(probas),
                "formulas": dict(formulas_loop),
                "feature_version": feature_version,
                "data_version": data_version,
                "as_of": stamp,
                "target_date": target_date,
                "provenance": provenance,
                "disclosure": DISCLOSURE,
                "record": record,
            }
            if as_of is None:
                try:
                    _forecast_cache_set(sym_upper, horizon, data_version, payload)
                except Exception:
                    pass
            out[int(horizon)] = payload
        return out


def get_forecast_service() -> ForecastService:
    global _service
    if _service is None:
        try:
            from backend.api.deps import get_market_service

            _service = ForecastService(market_service=get_market_service())
        except Exception:
            _service = ForecastService()
    return _service


def reset_forecast_service() -> None:  # test hook
    global _service
    _service = None
    try:
        clear_forecast_cache()
    except Exception:
        pass


__all__ = [
    "DISCLOSURE",
    "TREND_PERSISTENCE_VERSION",
    "ENSEMBLE_WEIGHTS",
    "CALIBRATION_SHRINKAGE",
    "PROB_FLOOR",
    "PROB_CAP",
    "SKILL_BRIER_THRESHOLD",
    "SKILL_ECE_THRESHOLD",
    "SKILL_MIN_N_EFFECTIVE",
    "MEMBER_VERSIONS",
    "MEMBER_FORMULAS",
    "ForecastService",
    "clear_forecast_cache",
    "get_forecast_service",
    "reset_forecast_service",
    "_adaptive_weights",
    "_weighted_mean",
    "_calibrate_prob",
    "_confidence_score",
    "_trend_persistence_signal",
    "_mean_reversion_signal",
]
