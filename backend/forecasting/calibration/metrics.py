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


def _bin_assignments(p: np.ndarray, n_bins: int) -> np.ndarray:
    """Vectorized bin ids replicating the legacy per-bin masks exactly.

    Bin 0 covers ``[e0, e1]`` (both edges inclusive); bins ``b > 0`` cover
    ``(e_b, e_{b+1}]``. ``np.digitize(p, inner_edges, right=True)`` assigns
    exactly those half-open intervals in one C pass (no Python bin loop).
    """
    n = int(n_bins)
    if n <= 1:
        return np.zeros(p.shape[0], dtype=np.intp)
    edges = np.linspace(0.0, 1.0, n + 1)
    return np.digitize(p, edges[1:-1], right=True).astype(np.intp)


def _binned_sums(
    y: np.ndarray, p: np.ndarray, n_bins: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(counts, sum_p, sum_y) per bin via ``np.bincount`` (single pass)."""
    n = int(n_bins)
    idx = _bin_assignments(p, n)
    counts = np.bincount(idx, minlength=n).astype(float)
    sum_p = np.bincount(idx, weights=p, minlength=n).astype(float)
    sum_y = np.bincount(idx, weights=y, minlength=n).astype(float)
    return counts, sum_p, sum_y


def calibration_error(
    y_true: Sequence[int | float], y_prob: Sequence[float], n_bins: int = 10
) -> float:
    """Expected calibration error with uniform bins (lower is better).

    Vectorized: one ``digitize`` + ``bincount`` pass instead of a Python
    loop over bins; identical bin edges and half-open semantics.

    NOTE (future per-user calibration hook): per-user reliability curves
    must be computed by grouping (y, p) pairs per user OUTSIDE this pure
    function (auth/tiers owned by another agent) and calling it per group.
    """
    if int(n_bins) < 1:
        raise ValueError("n_bins must be >= 1")
    y, p = _clean_prob_inputs(y_true, y_prob)
    n = int(n_bins)
    counts, sum_p, sum_y = _binned_sums(y, p, n)
    total = float(len(y))
    nonzero = counts > 0
    if not bool(nonzero.any()):
        return 0.0
    mean_p = np.zeros(n)
    mean_y = np.zeros(n)
    mean_p[nonzero] = sum_p[nonzero] / counts[nonzero]
    mean_y[nonzero] = sum_y[nonzero] / counts[nonzero]
    return float(np.sum(counts[nonzero] / total * np.abs(mean_p[nonzero] - mean_y[nonzero])))


def reliability_table(
    y_true: Sequence[int | float], y_prob: Sequence[float], n_bins: int = 10
) -> pd.DataFrame:
    """Per-bin counts, mean predicted probability and positive fraction.

    Vectorized over bins (same edges/semantics as :func:`calibration_error`);
    empty bins report NaN means exactly as before.
    """
    if int(n_bins) < 1:
        raise ValueError("n_bins must be >= 1")
    y, p = _clean_prob_inputs(y_true, y_prob)
    n = int(n_bins)
    edges = np.linspace(0.0, 1.0, n + 1)
    counts, sum_p, sum_y = _binned_sums(y, p, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        mean_p = sum_p / counts
        frac = sum_y / counts
    mean_p[counts == 0] = np.nan
    frac[counts == 0] = np.nan
    return pd.DataFrame({
        "bin_low": edges[:-1].astype(float),
        "bin_high": edges[1:].astype(float),
        "count": counts.astype(int),
        "mean_predicted": mean_p.astype(float),
        "fraction_positive": frac.astype(float),
    })


__all__ = ["brier_score", "calibration_error", "reliability_table"]
