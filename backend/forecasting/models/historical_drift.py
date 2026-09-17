"""Baseline 1: historical-drift model (Milestone 3).

Fits drift mu and daily volatility sigma on trailing daily log returns,
then scales by horizon: mu_h = mu*h, sigma_h = sigma*sqrt(h).
  * direction_probability = Phi(mu_h / sigma_h) (standard normal CDF via
    math.erf; degenerate sigma=0 falls back to sign(mu)).
  * expected_return_range = mu_h +/- z*sigma_h, reported in SIMPLE-return
    space via exp() (log-normal convention). NOTE: mid = exp(mu*h)-1 is the
    MEDIAN (not the mean exp(mu*h+0.5*sigma^2*h)-1; Jensen gap ~0.4pp at
    typical vol/horizon).
  * Plug-in correction: predictive variance inflates by (1 + h/n) to
    account for standard error of mu (true var ~= sigma^2*h + sigma^2*h^2/n).
    At n=250 this is ~1% at h=5, ~4% at h=21, ~12% at h=63.
Deterministic: closed form, no randomness. Reference baseline only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np
import pandas as pd

from ..common import (
    FORECAST_HORIZONS,
    TARGET_DIRECTION,
    TARGET_RETURN_RANGE,
    ForecastResult,
)
from ..features.features import FEATURE_VERSION

MODEL_NAME = "historical-drift"
MODEL_VERSION = "historical-drift-v1"
FORMULA_DIRECTION = (
    "P(up_h) = Phi(mu*h / (sigma*sqrt(h))); mu, sigma = mean/std of "
    "trailing daily log returns (sigma=0 -> sign(mu))"
)
FORMULA_RANGE = (
    "range_h = exp(mu*h +/- z*sigma*sqrt(h)) - 1 (log-normal band; "
    "mid = median exp(mu*h)-1, not mean)"
)


def _clean_returns(daily_returns) -> np.ndarray:
    values = pd.Series(daily_returns, dtype=float).dropna().to_numpy()
    if len(values) < 2:
        raise ValueError("need >= 2 valid daily returns to fit drift")
    if not np.isfinite(values).all():
        raise ValueError("daily returns must be finite")
    return values


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


@dataclass
class HistoricalDriftBaseline:
    """Closed-form drift baseline fitted on trailing daily log returns."""

    mean_daily: float | None = None
    std_daily: float | None = None
    n_obs: int = 0

    def fit(self, daily_returns) -> "HistoricalDriftBaseline":
        values = _clean_returns(daily_returns)
        self.mean_daily = float(np.mean(values))
        self.std_daily = float(np.std(values, ddof=1))
        self.n_obs = int(len(values))
        return self

    def _require_fit(self) -> tuple[float, float]:
        if self.mean_daily is None or self.std_daily is None:
            raise ValueError("model is not fitted; call fit() first")
        return self.mean_daily, self.std_daily

    def _predictive_scale(self, horizon: int) -> float:
        """sqrt(1 + h/n) plug-in inflation; 1.0 when n unknown/small."""
        try:
            n = int(self.n_obs)
            h = int(horizon)
        except (TypeError, ValueError):
            return 1.0
        if n < 2 or h < 1:
            return 1.0
        try:
            return math.sqrt(1.0 + float(h) / float(n))
        except (ValueError, OverflowError):
            return 1.0

    def direction_probability(
        self,
        horizon_days: int,
        as_of: str | None = None,
        data_version: str = "unspecified",
    ) -> ForecastResult:
        """P(close_{t+h} > close_t) under constant-drift log-normal walk."""
        try:
            horizon = int(horizon_days)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ValueError("horizon_days must be >= 1") from exc
        if horizon < 1:
            raise ValueError("horizon_days must be >= 1")
        mu, sigma = self._require_fit()
        horizon = int(horizon_days)
        if sigma == 0:
            proba = 1.0 if mu > 0 else (0.0 if mu < 0 else 0.5)
        else:
            _scale = self._predictive_scale(horizon)
            proba = _normal_cdf(mu * horizon / (sigma * math.sqrt(horizon) * _scale))
        return ForecastResult(
            TARGET_DIRECTION, horizon, float(min(max(proba, 0.0), 1.0)),
            FORMULA_DIRECTION, MODEL_NAME, MODEL_VERSION, FEATURE_VERSION,
            data_version, as_of,
        )

    def expected_return_range(
        self,
        horizon_days: int,
        z: float = 1.0,
        as_of: str | None = None,
        data_version: str = "unspecified",
    ) -> ForecastResult:
        """Symmetric z-band around the drift-implied forward return."""
        try:
            horizon = int(horizon_days)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ValueError("horizon_days must be >= 1") from exc
        if horizon < 1:
            raise ValueError("horizon_days must be >= 1")
        try:
            zf = float(z)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ValueError("z must be > 0") from exc
        if not zf > 0:
            raise ValueError("z must be > 0")
        mu, sigma = self._require_fit()
        _scale = self._predictive_scale(horizon)
        mid_log, half_log = mu * horizon, zf * sigma * math.sqrt(horizon) * _scale
        try:
            low = float(math.exp(mid_log - half_log) - 1.0)
            mid = float(math.exp(mid_log) - 1.0)
            high = float(math.exp(mid_log + half_log) - 1.0)
        except OverflowError as exc:
            raise ValueError("drift return band overflows finite range") from exc
        import math as _math2

        if not (_math2.isfinite(low) and _math2.isfinite(mid) and _math2.isfinite(high)):
            raise ValueError("drift return band overflows finite range")
        value = {
            "low": low,
            "mid": mid,
            "high": high,
            "z": zf,
        }
        return ForecastResult(
            TARGET_RETURN_RANGE, horizon, value,
            FORMULA_RANGE + f" with z={zf}",
            MODEL_NAME, MODEL_VERSION, FEATURE_VERSION, data_version, as_of,
        )

    def predict_all_horizons(
        self,
        horizons: Sequence[int] = FORECAST_HORIZONS,
        as_of: str | None = None,
        data_version: str = "unspecified",
    ) -> dict[int, ForecastResult]:
        """Direction probabilities for 1/7/14/21 trading days."""
        return {int(h): self.direction_probability(h, as_of, data_version)
                for h in horizons}


__all__ = ["HistoricalDriftBaseline", "MODEL_NAME", "MODEL_VERSION"]
