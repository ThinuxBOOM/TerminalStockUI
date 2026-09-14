"""Baseline 5: empirical quantile bands + regime/drawdown estimators (Milestone 3).

Distribution-free reference estimators over trailing history:
  * return_quantiles: overlapping h-day trailing log-return sums ->
    empirical (lower/median/upper) quantiles, mapped to simple-return
    space via exp(). Expected-return range target.
  * volatility_regime: latest trailing volatility bucketed against its own
    history quantiles -> "low" | "normal" | "high".
  * drawdown_probability: historical frequency of forward-h maximum
    drawdown breaching a threshold (e.g. 10%).
Deterministic (numpy quantiles, linear interpolation).
"""

from __future__ import annotations

from typing import Sequence

import numpy as np
import pandas as pd

from ..common import (
    FORECAST_HORIZONS,
    TARGET_DRAWDOWN,
    TARGET_RETURN_RANGE,
    TARGET_VOL_REGIME,
    ForecastResult,
)
from ..features.features import (
    FEATURE_VERSION,
    future_drawdown,
    log_returns,
)

MODEL_NAME = "empirical-quantiles"
MODEL_VERSION = "empirical-quantiles-v1"
FORMULA_BANDS = (
    "band_h(q) = exp(quantile_q(trailing overlapping h-day log-return sums)) - 1"
)
FORMULA_REGIME = (
    "regime = low if vol_trail <= Q33(history) else high if >= Q67 else normal"
)
FORMULA_DRAWDOWN = (
    "P(DD_h > threshold) = fraction of trailing bars whose forward-h "
    "max drawdown breached threshold"
)


def _clean_close(close) -> pd.Series:
    prices = pd.Series(close, dtype=float).dropna()
    if len(prices) < 2:
        raise ValueError("need >= 2 valid closes")
    if (prices <= 0).any() or not np.isfinite(prices.to_numpy()).all():
        raise ValueError("closes must be positive and finite")
    return prices


def return_quantiles(
    close,
    horizons: Sequence[int] = FORECAST_HORIZONS,
    lower: float = 0.1,
    upper: float = 0.9,
    min_obs: int = 30,
    as_of: str | None = None,
    data_version: str = "unspecified",
) -> dict[int, ForecastResult]:
    """Empirical (lower/median/upper) forward-return bands per horizon."""
    if not 0.0 < lower < 0.5 < upper < 1.0:
        raise ValueError("require 0 < lower < 0.5 < upper < 1")
    prices = _clean_close(close)
    lret = log_returns(prices).dropna()
    out: dict[int, ForecastResult] = {}
    for horizon in horizons:
        try:
            horizon = int(horizon)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ValueError("horizons must be >= 1") from exc
        if horizon < 1:
            raise ValueError("horizons must be >= 1")
        # Short-circuit thin history before the rolling-sum (same error the
        # post-compute gate would raise, without O(n) work).
        try:
            need = int(min_obs) + horizon  # type: ignore[arg-type]
        except (TypeError, ValueError):
            need = 30 + horizon
        if len(lret) < need:
            # Fall through to the exact gate below so the message stays
            # byte-identical (len(sums) based); only skip when provably thin.
            if len(lret) < horizon:
                raise ValueError(
                    f"horizon {horizon}: only 0 trailing windows, "
                    f"need >= {min_obs}")
        sums = lret.rolling(horizon, min_periods=horizon).sum().dropna()
        if len(sums) < min_obs:
            raise ValueError(
                f"horizon {horizon}: only {len(sums)} trailing windows, "
                f"need >= {min_obs}")
        qs = np.quantile(sums.to_numpy(), [lower, 0.5, upper])
        value = {"low": float(np.exp(qs[0]) - 1.0),
                 "median": float(np.exp(qs[1]) - 1.0),
                 "high": float(np.exp(qs[2]) - 1.0),
                 "lower_q": float(lower), "upper_q": float(upper),
                 "n_windows": int(len(sums))}
        out[horizon] = ForecastResult(
            TARGET_RETURN_RANGE, horizon, value,
            FORMULA_BANDS + f" with q=({lower}, 0.5, {upper})",
            MODEL_NAME, MODEL_VERSION, FEATURE_VERSION, data_version, as_of,
        )
    return out


def volatility_regime(
    close,
    trailing_days: int = 21,
    annualization: int = 252,
    low_q: float = 1.0 / 3.0,
    high_q: float = 2.0 / 3.0,
    as_of: str | None = None,
    data_version: str = "unspecified",
) -> ForecastResult:
    """Bucket current trailing volatility vs its own history."""
    try:
        trailing = int(trailing_days)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError("trailing_days must be >= 2") from exc
    if trailing < 2:
        raise ValueError("trailing_days must be >= 2")
    if not 0.0 < low_q < high_q < 1.0:
        raise ValueError("require 0 < low_q < high_q < 1")
    prices = _clean_close(close)
    vols = (log_returns(prices).rolling(trailing,
                                        min_periods=trailing).std(ddof=1)
            * np.sqrt(float(annualization))).dropna()
    if len(vols) < 2:
        raise ValueError("not enough history for a volatility regime")
    # Single-pass quantiles (one sort, identical values to two calls).
    lo, hi = (float(v) for v in np.quantile(vols.to_numpy(), [low_q, high_q]))
    latest = float(vols.iloc[-1])
    # Guard non-finite vol (never leaks NaN/inf; finite history implies finite).
    if not (latest == latest and latest not in (float("inf"), float("-inf"))):
        raise ValueError("trailing volatility is non-finite")
    regime = "low" if latest <= lo else ("high" if latest >= hi else "normal")
    return ForecastResult(
        TARGET_VOL_REGIME, trailing,
        {"regime": regime, "trailing_vol": latest,
         "low_threshold": lo, "high_threshold": hi},
        FORMULA_REGIME, MODEL_NAME, MODEL_VERSION, FEATURE_VERSION,
        data_version, as_of,
    )


def drawdown_probability(
    close,
    horizon_days: int,
    threshold: float = 0.10,
    min_obs: int = 30,
    as_of: str | None = None,
    data_version: str = "unspecified",
) -> ForecastResult:
    """Historical breach frequency of a forward-h drawdown threshold."""
    try:
        horizon = int(horizon_days)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError("horizon_days must be >= 1") from exc
    if horizon < 1:
        raise ValueError("horizon_days must be >= 1")
    try:
        thresh = float(threshold)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError("threshold must be in (0, 1)") from exc
    if not 0.0 < thresh < 1.0:
        raise ValueError("threshold must be in (0, 1)")
    prices = _clean_close(close)
    forwards = future_drawdown(prices, horizon).dropna()
    if len(forwards) < min_obs:
        raise ValueError(f"only {len(forwards)} observable windows, need >= {min_obs}")
    breaches = forwards <= -thresh
    proba = float(breaches.mean())
    return ForecastResult(
        TARGET_DRAWDOWN, horizon,
        {"probability": proba, "threshold": thresh,
         "n_windows": int(len(forwards)),
         "n_breaches": int(breaches.sum())},
        FORMULA_DRAWDOWN, MODEL_NAME, MODEL_VERSION, FEATURE_VERSION,
        data_version, as_of,
    )


__all__ = ["return_quantiles", "volatility_regime", "drawdown_probability",
           "MODEL_NAME", "MODEL_VERSION"]
