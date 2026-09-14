"""Baseline 2: trailing-momentum model (Milestone 3).

Naive reference: trailing `trailing_days` LOG return R (log(P_t/P_{t-63}),
consistent with the log-vol denominator) and its realized volatility sigma
give z = R / (sigma*sqrt(trailing_days)); the direction probability is the
logistic map 1/(1+exp(-z)) with fixed gain k=1. Uptrend -> > 0.5,
downtrend -> < 0.5, flat -> 0.5. Deterministic.

Horizon note: predict_all_horizons returns the SAME level for 5/21/63
(horizon-agnostic stub). Do not read it as horizon-calibrated: forward
variance should pull long-horizon P toward 0.5. The horizon arg only labels
output.
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
    "z = R_trail_log / (sigma_trail*sqrt(trailing_days)); "
    "R_trail_log = log(P_t/P_{t-trailing}); "
    "P(up) = 1/(1+exp(-k*z)), k=1 (naive momentum baseline, horizon-agnostic)"
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
        # Log trailing return for consistency with the log-vol denominator
        # (simple/log differ ~1.23x on +50% runs; mixing inflates rallies).
        self.trailing_return = float(math.log(float(tail.iloc[-1] / tail.iloc[0])))
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
        try:
            horizon = int(horizon_days)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ValueError("horizon_days must be >= 1") from exc
        if horizon < 1:
            raise ValueError("horizon_days must be >= 1")
        if self.trailing_return is None or self.trailing_vol is None:
            raise ValueError("model is not fitted; call fit() first")
        # Guard non-finite fitted state (never leaks NaN/inf to the wire).
        try:
            tr = float(self.trailing_return)
            tv = float(self.trailing_vol)
        except (TypeError, ValueError) as exc:
            raise ValueError("model state is non-numeric") from exc
        if not (math.isfinite(tr) and math.isfinite(tv)):
            raise ValueError("model state must be finite")
        denom = tv * math.sqrt(self.trailing_days)
        if denom == 0 or not math.isfinite(denom):
            proba = 1.0 if tr > 0 else (
                0.0 if tr < 0 else 0.5)
        else:
            # Clip z: exp() overflows past ~709; |z| <= 50 already saturates
            # the sigmoid to 0/1 within float precision. Deterministic.
            z = max(min(GAIN * tr / denom, 50.0), -50.0)
            proba = 1.0 / (1.0 + math.exp(-z))
        return ForecastResult(
            TARGET_DIRECTION, horizon, float(proba),
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
