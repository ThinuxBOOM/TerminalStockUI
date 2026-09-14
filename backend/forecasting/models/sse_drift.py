"""SSE-tuned drift baseline (Milestone 6).

Deterministic, no AI, no network. Same constant-drift log-normal walk as
``historical-drift-v1`` but adapted to Shanghai (XSHG) microstructure:

Assumptions (documented, no live data fabricated):
  * Price limits censor tails: generic A-share +/-10% daily limit truncates
    observable single-day moves. Raw sample volatility therefore understates
    latent volatility, so fitted daily log-returns are winsorized (clipped)
    to the limit-implied log band and forecast bands are widened.
  * T+1 settlement (shares bought at T cannot be sold until T+1) dampens
    same-day reversal; the drift fit uses the full trailing window without
    extra down-weighting — documented, not separately parameterized.
  * Lunch break (09:30-11:30 / 13:00-15:00, 90-min gap): daily bars cannot
    observe the intraday gap, so no lunch-break adjustment is applied here;
    intraday extensions should exclude the gap from realized-vol clocks.

Formulas:
  * Winsorize: ``r_clip = clip(r, log(1 - limit_pct), log(1 + limit_pct))``
    with default ``limit_pct = 0.10`` -> ``[log(0.9), log(1.1)]``.
  * Fit ``mu, sigma`` = mean/std (ddof=1) of clipped trailing log-returns.
  * Direction: ``P(up_h) = Phi(mu*h / (sigma*sqrt(h)))`` (``sigma = 0`` falls
    back to ``sign(mu)``), identical to the US drift baseline.
  * Range: ``exp(mu*h +/- z*sigma*sqrt(h)) - 1`` with a WIDER default
    ``z = 1.28`` (~80% band vs 68% at z=1.0) to reflect limit-censored tails.
    Callers may pass any ``z > 0`` explicitly.
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
from ..features.sse import SSE_FEATURE_VERSION

MODEL_NAME = "sse-drift"
MODEL_VERSION = "sse-drift-v1"
FORMULA_DIRECTION = (
    "P(up_h) = Phi(mu*h / (sigma*sqrt(h))); mu, sigma = mean/std of "
    "winsorized trailing daily log returns clipped to "
    "[log(1-limit), log(1+limit)] (sigma=0 -> sign(mu))"
)
FORMULA_RANGE = (
    "range_h = exp(mu*h +/- z*sigma*sqrt(h)) - 1 with wider default z=1.28 "
    "(limit-censored tails)"
)
PRICE_LIMIT_PCT = 0.10
SSE_DEFAULT_Z = 1.28


def winsorize_returns(daily_returns, limit_pct: float = PRICE_LIMIT_PCT) -> np.ndarray:
    """Clip daily log-returns to the limit-implied log band (deterministic)."""
    try:
        lp = float(limit_pct)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"limit_pct must be in (0, 1), got {limit_pct!r}") from exc
    if not 0.0 < lp < 1.0:
        raise ValueError(f"limit_pct must be in (0, 1), got {limit_pct!r}")
    values = pd.Series(daily_returns, dtype=float).dropna().to_numpy()
    if len(values) < 2:
        raise ValueError("need >= 2 valid daily returns to fit SSE drift")
    if not np.isfinite(values).all():
        raise ValueError("daily returns must be finite")
    lo = math.log(1.0 - lp)
    hi = math.log(1.0 + lp)
    return np.clip(values, lo, hi)


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


@dataclass
class SseDriftBaseline:
    """SSE-tuned drift baseline on winsorized daily log-returns."""

    mean_daily: float | None = None
    std_daily: float | None = None
    n_obs: int = 0
    limit_pct: float = PRICE_LIMIT_PCT

    def fit(self, daily_returns, limit_pct: float = PRICE_LIMIT_PCT) -> "SseDriftBaseline":
        clipped = winsorize_returns(daily_returns, limit_pct=float(limit_pct))
        self.mean_daily = float(np.mean(clipped))
        self.std_daily = float(np.std(clipped, ddof=1))
        self.n_obs = int(len(clipped))
        self.limit_pct = float(limit_pct)
        return self

    def _require_fit(self) -> tuple[float, float]:
        if self.mean_daily is None or self.std_daily is None:
            raise ValueError("model is not fitted; call fit() first")
        return self.mean_daily, self.std_daily

    def direction_probability(
        self,
        horizon_days: int,
        as_of: str | None = None,
        data_version: str = "unspecified",
    ) -> ForecastResult:
        """P(close_{t+h} > close_t) under the winsorized drift walk."""
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
            proba = _normal_cdf(mu * horizon / (sigma * math.sqrt(horizon)))
        return ForecastResult(
            TARGET_DIRECTION, horizon, float(min(max(proba, 0.0), 1.0)),
            FORMULA_DIRECTION, MODEL_NAME, MODEL_VERSION, SSE_FEATURE_VERSION,
            data_version, as_of,
        )

    def expected_return_range(
        self,
        horizon_days: int,
        z: float = SSE_DEFAULT_Z,
        as_of: str | None = None,
        data_version: str = "unspecified",
    ) -> ForecastResult:
        """Wider z-band around the drift-implied forward return."""
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
        mid_log, half_log = mu * horizon, zf * sigma * math.sqrt(horizon)
        try:
            low = float(math.exp(mid_log - half_log) - 1.0)
            mid = float(math.exp(mid_log) - 1.0)
            high = float(math.exp(mid_log + half_log) - 1.0)
        except OverflowError as exc:
            raise ValueError("SSE drift return band overflows finite range") from exc
        import math as _math2

        if not (_math2.isfinite(low) and _math2.isfinite(mid) and _math2.isfinite(high)):
            raise ValueError("SSE drift return band overflows finite range")
        value = {
            "low": low,
            "mid": mid,
            "high": high,
            "z": zf,
        }
        return ForecastResult(
            TARGET_RETURN_RANGE, horizon, value,
            FORMULA_RANGE + f" with z={zf}",
            MODEL_NAME, MODEL_VERSION, SSE_FEATURE_VERSION, data_version, as_of,
        )

    def predict_all_horizons(
        self,
        horizons: Sequence[int] = FORECAST_HORIZONS,
        as_of: str | None = None,
        data_version: str = "unspecified",
    ) -> dict[int, ForecastResult]:
        """Direction probabilities for 5/21/63 trading days."""
        return {int(h): self.direction_probability(h, as_of, data_version)
                for h in horizons}


__all__ = [
    "SseDriftBaseline",
    "winsorize_returns",
    "MODEL_NAME",
    "MODEL_VERSION",
    "SSE_DEFAULT_Z",
    "PRICE_LIMIT_PCT",
]
