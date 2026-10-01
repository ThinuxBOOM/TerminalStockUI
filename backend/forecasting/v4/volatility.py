"""Volatility forecasts and the return range / drop risk built on them.

Log-HAR: log of the mean daily variance over the next h sessions regressed
on the logs of trailing 5/21/63/252-day variances, pooled across symbols.
The range uses empirical quantiles of returns standardized by the forecast
volatility (fat tails come from the data, not a normal assumption).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

VAR_INPUTS = ("v5", "v21", "v63", "v252")
#: Quantile levels served (the 80% band is q10..q90).
QUANTILES = (0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95)
_EPS = 1e-10


def _log_inputs(df: pd.DataFrame) -> np.ndarray:
    x = np.log(np.maximum(df[list(VAR_INPUTS)].to_numpy(dtype="float64"), _EPS))
    return np.column_stack([np.ones(len(x)), x])


@dataclass
class VolModel:
    """Per-horizon log-HAR coefficients and standardized-return quantiles."""

    horizon: int
    coef: list[float] = field(default_factory=list)
    resid_var: float = 0.0
    z_quantiles: dict[str, float] = field(default_factory=dict)
    dd_coef: list[float] = field(default_factory=list)

    def daily_var(self, df: pd.DataFrame) -> np.ndarray:
        """Forecast mean daily variance over the next h sessions."""
        pred = _log_inputs(df) @ np.asarray(self.coef)
        return np.exp(pred + 0.5 * self.resid_var)

    def sigma(self, df: pd.DataFrame) -> np.ndarray:
        """Forecast h-day log-return standard deviation."""
        return np.sqrt(self.horizon * self.daily_var(df))

    def quantiles(self, df: pd.DataFrame) -> dict[str, np.ndarray]:
        s = self.sigma(df)
        return {q: s * z for q, z in self.z_quantiles.items()}

    def drawdown_prob(self, df: pd.DataFrame) -> np.ndarray:
        """P(a 10%+ close-to-close drop within h sessions), logistic in log sigma."""
        a, b = self.dd_coef
        return 1.0 / (1.0 + np.exp(-(a + b * np.log(np.maximum(self.sigma(df), _EPS)))))

    def to_dict(self) -> dict:
        return {"horizon": self.horizon, "coef": self.coef, "resid_var": self.resid_var,
                "z_quantiles": self.z_quantiles, "dd_coef": self.dd_coef}

    @classmethod
    def from_dict(cls, d: dict) -> "VolModel":
        return cls(int(d["horizon"]), list(d["coef"]), float(d["resid_var"]),
                   {str(k): float(v) for k, v in d["z_quantiles"].items()}, list(d["dd_coef"]))


def fit(train: pd.DataFrame, horizon: int) -> VolModel:
    """Fit on rows with complete inputs and an observed h-day outcome."""
    cols = list(VAR_INPUTS) + [f"var_{horizon}", f"fwd_{horizon}", f"bigdd_{horizon}"]
    df = train[cols].dropna()
    df = df[(df[list(VAR_INPUTS)] > 0).all(axis=1)]
    X = _log_inputs(df)
    y = np.log(np.maximum(df[f"var_{horizon}"].to_numpy(dtype="float64") / horizon, _EPS))
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid_var = float(np.var(y - X @ coef))
    model = VolModel(horizon, [float(c) for c in coef], resid_var)
    z = df[f"fwd_{horizon}"].to_numpy(dtype="float64") / model.sigma(df)
    model.z_quantiles = {f"{q:.2f}": float(np.quantile(z, q)) for q in QUANTILES}
    # Drop risk: one-feature logistic on log(sigma_h), pooled.
    from sklearn.linear_model import LogisticRegression

    ls = np.log(model.sigma(df)).reshape(-1, 1)
    yb = df[f"bigdd_{horizon}"].to_numpy()
    if 0 < yb.mean() < 1:
        lr = LogisticRegression(C=10.0).fit(ls, yb)
        model.dd_coef = [float(lr.intercept_[0]), float(lr.coef_[0][0])]
    else:
        model.dd_coef = [float(np.log(max(yb.mean(), 1e-4) / max(1 - yb.mean(), 1e-4))), 0.0]
    return model


def ewma_sigma(logret: pd.Series, horizon: int, lam: float = 0.94) -> pd.Series:
    """RiskMetrics EWMA daily volatility scaled to h days (baseline)."""
    var = (logret ** 2).ewm(alpha=1 - lam, adjust=False).mean()
    return np.sqrt(horizon * var)


def interval_score(lo: np.ndarray, hi: np.ndarray, y: np.ndarray, alpha: float = 0.2) -> np.ndarray:
    """Winkler/interval score for a (1 - alpha) interval; lower is better."""
    return (hi - lo) + (2 / alpha) * np.maximum(lo - y, 0) + (2 / alpha) * np.maximum(y - hi, 0)
