"""Forecast calibration metrics (Milestone 3).

Brier score and expected calibration error (ECE) over probability
forecasts, plus a reliability table for the calibration dashboard.
Pure numpy/pandas: deterministic, no network.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np
import pandas as pd

BRIER_FORMULA = "Brier = mean((p_i - y_i)^2); 0 = perfect, 0.25 = coin-flip baseline"
ECE_FORMULA = (
    "ECE = sum_b (|bin_b|/n * |mean_p_b - frac_pos_b|) over equal-width bins"
)


def _clean_prob_inputs(y_true, y_prob) -> tuple[np.ndarray, np.ndarray]:
    try:
        y = np.asarray(y_true, dtype=float)
        p = np.asarray(y_prob, dtype=float)
    except (ValueError, TypeError):
        raise ValueError("y_true and y_prob must be numeric sequences")
    if y.shape != p.shape:
        raise ValueError(f"shape mismatch: y_true {y.shape} vs y_prob {p.shape}")
    if y.size == 0:
        raise ValueError("inputs must be non-empty")
    if not np.isin(y, [0.0, 1.0]).all():
        raise ValueError("y_true must contain only 0/1 labels")
    if not np.isfinite(p).all() or ((p < 0.0) | (p > 1.0)).any():
        raise ValueError("y_prob must be finite probabilities in [0, 1]")
    return y, p


def brier_score(y_true: Sequence[int | float], y_prob: Sequence[float]) -> float:
    """Mean squared error of probability forecasts (lower is better)."""
    y, p = _clean_prob_inputs(y_true, y_prob)
    return float(np.mean((p - y) ** 2))


def calibration_error(
    y_true: Sequence[int | float], y_prob: Sequence[float], n_bins: int = 10
) -> float:
    """Expected calibration error with uniform bins (lower is better)."""
    if int(n_bins) < 1:
        raise ValueError("n_bins must be >= 1")
    y, p = _clean_prob_inputs(y_true, y_prob)
    edges = np.linspace(0.0, 1.0, int(n_bins) + 1)
    error, n = 0.0, len(y)
    for b in range(int(n_bins)):
        lo, hi = edges[b], edges[b + 1]
        mask = (p > lo) & (p <= hi) if b else (p >= lo) & (p <= hi)
        if not mask.any():
            continue
        error += (mask.sum() / n) * abs(p[mask].mean() - y[mask].mean())
    return float(error)


def reliability_table(
    y_true: Sequence[int | float], y_prob: Sequence[float], n_bins: int = 10
) -> pd.DataFrame:
    """Per-bin counts, mean predicted probability and positive fraction."""
    if int(n_bins) < 1:
        raise ValueError("n_bins must be >= 1")
    y, p = _clean_prob_inputs(y_true, y_prob)
    edges = np.linspace(0.0, 1.0, int(n_bins) + 1)
    rows = []
    for b in range(int(n_bins)):
        lo, hi = edges[b], edges[b + 1]
        mask = (p > lo) & (p <= hi) if b else (p >= lo) & (p <= hi)
        rows.append({
            "bin_low": float(lo), "bin_high": float(hi),
            "count": int(mask.sum()),
            "mean_predicted": float(p[mask].mean()) if mask.any() else float("nan"),
            "fraction_positive": float(y[mask].mean()) if mask.any() else float("nan"),
        })
    return pd.DataFrame(rows)


__all__ = ["brier_score", "calibration_error", "reliability_table"]
