"""Baseline 2: trailing-momentum model (Milestone 3).

Naive reference: trailing `trailing_days` simple return R and its realized
volatility sigma give z = R / (sigma*sqrt(trailing_days)); the direction
probability is the logistic map 1/(1+exp(-z)) with fixed gain k=1.
Uptrend -> > 0.5, downtrend -> < 0.5, flat -> 0.5. Deterministic.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np
import pandas as pd

from ..common import FORECAST_HORIZONS, TARGET_DIRECTION, ForecastResult
from ..features.features import FEATURE_VERSION

MODEL_NAME = "momentum"
MODEL_VERSION = "momentum-v1"
FORMULA = (
    "z = R_trail / (sigma_trail*sqrt(trailing_days)); "
    "P(up) = 1/(1+exp(-k*z)), k=1 (naive momentum baseline)"
)
GAIN = 1.0


@dataclass
class MomentumBaseline:
    """Fixed-form momentum baseline over a trailing window."""

    trailing_days: int = 63
    trailing_return: float | None = None
    trailing_vol: float | None = None

    def fit(self, close) -> "MomentumBaseline":
        if int(self.trailing_days) < 2:
            raise ValueError("trailing_days must be >= 2")
        prices = pd.Series(close, dtype=float).dropna()
        window = int(self.trailing_days)
        if len(prices) < window + 1:
            raise ValueError(f"need >= {window + 1} closes, got {len(prices)}")
        tail = prices.iloc[-(window + 1):]
        if (tail <= 0).any() or not np.isfinite(tail.to_numpy()).all():
            raise ValueError("closes must be positive and finite")
        self.trailing_return = float(tail.iloc[-1] / tail.iloc[0] - 1.0)
        lret = np.log(tail.to_numpy()[1:] / tail.to_numpy()[:-1])
        self.trailing_vol = float(np.std(lret, ddof=1)) if len(lret) > 1 else 0.0
        return self

    def direction_probability(
        self,
        horizon_days: int,
        as_of: str | None = None,
        data_version: str = "unspecified",
    ) -> ForecastResult:
        """Logistic map of the trailing risk-adjusted return."""
        if int(horizon_days) < 1:
            raise ValueError("horizon_days must be >= 1")
        if self.trailing_return is None or self.trailing_vol is None:
            raise ValueError("model is not fitted; call fit() first")
        denom = self.trailing_vol * math.sqrt(self.trailing_days)
        if denom == 0:
            proba = 1.0 if self.trailing_return > 0 else (
                0.0 if self.trailing_return < 0 else 0.5)
        else:
            # Clip z: exp() overflows past ~709; |z| <= 50 already saturates
            # the sigmoid to 0/1 within float precision. Deterministic.
            z = max(min(GAIN * self.trailing_return / denom, 50.0), -50.0)
            proba = 1.0 / (1.0 + math.exp(-z))
        return ForecastResult(
            TARGET_DIRECTION, int(horizon_days), float(proba),
            FORMULA, MODEL_NAME, MODEL_VERSION, FEATURE_VERSION,
            data_version, as_of,
        )

    def predict_all_horizons(
        self,
        horizons: Sequence[int] = FORECAST_HORIZONS,
        as_of: str | None = None,
        data_version: str = "unspecified",
    ) -> dict[int, ForecastResult]:
        """Direction probabilities for 5/21/63 trading days (same level)."""
        return {int(h): self.direction_probability(h, as_of, data_version)
                for h in horizons}


__all__ = ["MomentumBaseline", "MODEL_NAME", "MODEL_VERSION"]
