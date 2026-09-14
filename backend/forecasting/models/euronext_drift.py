"""Euronext-tuned drift baseline (Milestone 7).

Deterministic, no AI, no network. Same constant-drift log-normal walk as
``historical-drift-v1`` but adapted to Euronext (XPAR/XAMS/XBRU)
microstructure:

Assumptions (documented, no live data fabricated):
  * No daily price limits: unlike SSE generic A-share +/-10% limits (which
    censor tails and motivate limit-implied winsorization), Euronext has no
    hard daily limit band. Winsorization here is a STATISTICAL robustness
    cap only (default +/-15% simple move), wider than the SSE +/-10%
    limit-implied band, to damp single-day outliers (halts / corporate
    actions / data errors) without censoring genuine Euronext tails.
  * Continuous session 09:00-17:30 local with no lunch break: daily bars
    aggregate the full continuous session, so no lunch-gap realized-vol
    adjustment is applied here.
  * Euronext tick-size regimes (MiFID II / Euronext tick tables) are noted
    but not modeled: daily closes cannot observe tick-constrained
    micro-price dynamics.
  * Settlement (T+2, moving to T+1 under CSDR) is documented, not
    separately parameterized; the drift fit uses the full trailing window
    without extra down-weighting.

Formulas:
  * Winsorize: ``r_clip = clip(r, log(1 - cap_pct), log(1 + cap_pct))``
    with default ``cap_pct = 0.15`` -> ``[log(0.85), log(1.15)]``.
    This cap is WIDER than SSE (0.10) and is robustness-only since
    Euronext imposes no hard limit.
  * Fit ``mu, sigma`` = mean/std (ddof=1) of clipped trailing log-returns.
  * Direction: ``P(up_h) = Phi(mu*h / (sigma*sqrt(h)))`` (``sigma = 0`` falls
    back to ``sign(mu)``), identical to the US drift baseline.
  * Range: ``exp(mu*h +/- z*sigma*sqrt(h)) - 1`` with default
    ``z = 1.15`` (between the US 68% band at z=1.0 and the wider SSE
    limit-censored band at z=1.28). Callers may pass any ``z > 0``.
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
from ..features.euronext import EUX_FEATURE_VERSION

MODEL_NAME = "eux-drift"
MODEL_VERSION = "eux-drift-v1"
FORMULA_DIRECTION = (
    "P(up_h) = Phi(mu*h / (sigma*sqrt(h))); mu, sigma = mean/std of "
    "winsorized trailing daily log returns clipped to "
    "[log(1-cap), log(1+cap)] (sigma=0 -> sign(mu))"
)
FORMULA_RANGE = (
    "range_h = exp(mu*h +/- z*sigma*sqrt(h)) - 1 with default z=1.15 "
    "(robustness cap, no hard Euronext limits)"
)
WINSOR_CAP_PCT = 0.15
EUX_DEFAULT_Z = 1.15


def winsorize_returns(daily_returns, cap_pct: float = WINSOR_CAP_PCT) -> np.ndarray:
    """Clip daily log-returns to the robustness cap band (deterministic)."""
    try:
        cp = float(cap_pct)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"cap_pct must be in (0, 1), got {cap_pct!r}") from exc
    if not 0.0 < cp < 1.0:
        raise ValueError(f"cap_pct must be in (0, 1), got {cap_pct!r}")
    values = pd.Series(daily_returns, dtype=float).dropna().to_numpy()
    if len(values) < 2:
        raise ValueError("need >= 2 valid daily returns to fit Euronext drift")
    if not np.isfinite(values).all():
        raise ValueError("daily returns must be finite")
    lo = math.log(1.0 - cp)
    hi = math.log(1.0 + cp)
    return np.clip(values, lo, hi)


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


@dataclass
class EuxDriftBaseline:
    """Euronext-tuned drift baseline on winsorized daily log-returns."""

    mean_daily: float | None = None
    std_daily: float | None = None
    n_obs: int = 0
    cap_pct: float = WINSOR_CAP_PCT
    # Unclipped sigma for direction (clipping shrinks sigma and inflates
    # |z|; bands keep the clipped sigma for robustness). Mirrors SSE fix.
    std_unclipped: float | None = None

    def fit(self, daily_returns, cap_pct: float = WINSOR_CAP_PCT) -> "EuxDriftBaseline":
        clipped = winsorize_returns(daily_returns, cap_pct=float(cap_pct))
        raw = pd.Series(daily_returns, dtype=float).dropna().to_numpy()
        self.mean_daily = float(np.mean(clipped))
        self.std_daily = float(np.std(clipped, ddof=1))
        try:
            self.std_unclipped = float(np.std(raw, ddof=1)) if len(raw) >= 2 else self.std_daily
        except (TypeError, ValueError):
            self.std_unclipped = self.std_daily
        self.n_obs = int(len(clipped))
        self.cap_pct = float(cap_pct)
        return self

    def _require_fit(self) -> tuple[float, float]:
        if self.mean_daily is None or self.std_daily is None:
            raise ValueError("model is not fitted; call fit() first")
        return self.mean_daily, self.std_daily

    def _direction_sigma(self) -> float:
        """Sigma for P(up): unclipped (latent) vol, not the shrunk clipped one."""
        if self.std_unclipped is not None and math.isfinite(self.std_unclipped) and self.std_unclipped > 0:
            return self.std_unclipped
        _, clipped = self._require_fit()
        return clipped

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
        # Direction uses the unclipped (latent-vol) sigma: the clipped sigma
        # understates tails and would overstate |z| ~ sqrt(h).
        sigma_dir = self._direction_sigma()
        if sigma_dir == 0:
            proba = 1.0 if mu > 0 else (0.0 if mu < 0 else 0.5)
        else:
            proba = _normal_cdf(mu * horizon / (sigma_dir * math.sqrt(horizon)))
        return ForecastResult(
            TARGET_DIRECTION, horizon, float(min(max(proba, 0.0), 1.0)),
            FORMULA_DIRECTION, MODEL_NAME, MODEL_VERSION, EUX_FEATURE_VERSION,
            data_version, as_of,
        )

    def expected_return_range(
        self,
        horizon_days: int,
        z: float = EUX_DEFAULT_Z,
        as_of: str | None = None,
        data_version: str = "unspecified",
    ) -> ForecastResult:
        """z-band around the drift-implied forward return (default z=1.15)."""
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
            raise ValueError("Euronext drift return band overflows finite range") from exc
        import math as _math2

        if not (_math2.isfinite(low) and _math2.isfinite(mid) and _math2.isfinite(high)):
            raise ValueError("Euronext drift return band overflows finite range")
        value = {
            "low": low,
            "mid": mid,
            "high": high,
            "z": zf,
        }
        return ForecastResult(
            TARGET_RETURN_RANGE, horizon, value,
            FORMULA_RANGE + f" with z={zf}",
            MODEL_NAME, MODEL_VERSION, EUX_FEATURE_VERSION, data_version, as_of,
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


# Backwards-compatible alias (same class, Euronext-spelled name).
EuronextDriftBaseline = EuxDriftBaseline


__all__ = [
    "EuxDriftBaseline",
    "EuronextDriftBaseline",
    "winsorize_returns",
    "MODEL_NAME",
    "MODEL_VERSION",
    "EUX_DEFAULT_Z",
    "WINSOR_CAP_PCT",
]
