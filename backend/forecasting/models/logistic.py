"""Baseline 3: regularized logistic-regression direction model (Milestone 3).

One L2-regularized LogisticRegression per horizon (5/21/63d) on the v1
feature frame. Fixed hyperparameters (C=1.0) and random_state=0 make fits
bit-deterministic for identical inputs. Labels are forward direction
indicators built point-in-time (row t uses only closes <= t+h for the
label, features use only data <= t); training must still go through the
walk-forward splitter + no-leakage guard.
"""

from __future__ import annotations

from typing import Any, Sequence

import numpy as np
import pandas as pd

try:
    from sklearn.linear_model import LogisticRegression
except ImportError:  # pragma: no cover - optional dep; degraded at fit time
    LogisticRegression = None  # type: ignore[assignment]


def _require_sklearn() -> None:
    if LogisticRegression is None:
        raise ImportError(
            "scikit-learn is not installed; LogisticDirectionModel.fit() is "
            "unavailable. Install scikit-learn or rely on the drift+momentum "
            "ensemble fallback (ForecastService degrades automatically)."
        )

from ..common import FORECAST_HORIZONS, TARGET_DIRECTION, ForecastResult
from ..features.features import (
    FEATURE_COLUMNS,
    FEATURE_VERSION,
    direction_label,
)

MODEL_NAME = "logistic-direction"
MODEL_VERSION = "logistic-direction-v2"
FORMULA = (
    "P(up_h) = sigmoid(w_h . z + b_h) where z = (x - mean_h)/scale_h "
    "(train-only standardization); L2 LogisticRegression(C=1.0, "
    "random_state=0) per horizon on v1 features"
)
MIN_SAMPLES = 20


class LogisticDirectionModel:
    """Regularized logistic regression direction classifier per horizon."""

    def __init__(self, horizons: Sequence[int] = FORECAST_HORIZONS,
                 C: float = 1.0) -> None:
        self.horizons = tuple(int(h) for h in horizons)
        if any(h < 1 for h in self.horizons):
            raise ValueError("horizons must be >= 1")
        if not float(C) > 0:
            raise ValueError("C must be > 0")
        self.C = float(C)
        self.models_: dict[int, Any] = {}
        self.n_train_: dict[int, int] = {}
        self.feature_columns_: list[str] = []
        # Train-only standardization per horizon (fixes scale-dependent L2:
        # RSI 0-100 vs ret ~0.02). Stored as plain arrays for determinism.
        self.scaler_mean_: dict[int, Any] = {}
        self.scaler_scale_: dict[int, Any] = {}

    def fit(self, features: pd.DataFrame, close: pd.Series) -> "LogisticDirectionModel":
        """Fit one classifier per horizon on label-observable rows."""
        _require_sklearn()
        frame = pd.DataFrame(features)
        if len(frame) == 0:
            raise ValueError("feature frame is empty")
        closes = pd.Series(close, dtype=float)
        both = frame.join(closes.rename("__close__"), how="inner")
        if len(both) == 0:
            raise ValueError("features and close share no index labels")
        X = both[frame.columns].to_numpy(dtype=float)
        if not np.isfinite(X).all():
            raise ValueError("feature frame contains NaN/inf; run build_features first")
        fitted: dict[int, Any] = {}
        counts: dict[int, int] = {}
        means: dict[int, Any] = {}
        scales: dict[int, Any] = {}
        for horizon in self.horizons:
            labels = direction_label(both["__close__"], horizon)
            mask = labels.notna().to_numpy()
            Xh, yh = X[mask], labels.to_numpy()[mask].astype(int)
            if len(yh) < MIN_SAMPLES:
                raise ValueError(
                    f"horizon {horizon}: only {len(yh)} label-observable rows, "
                    f"need >= {MIN_SAMPLES}")
            if len(np.unique(yh)) < 2:
                raise ValueError(
                    f"horizon {horizon}: labels are single-class; cannot fit")
            # Standardize on train rows only (per-horizon mask).
            mu = Xh.mean(axis=0)
            sd = Xh.std(axis=0, ddof=0)
            # Zero-variance column -> scale 1 (no-op, avoids div-by-zero).
            sd = np.where(np.isfinite(sd) & (sd > 1e-12), sd, 1.0)
            Xh_s = (Xh - mu) / sd
            clf = LogisticRegression(C=self.C, max_iter=2000, random_state=0)
            clf.fit(Xh_s, yh)
            fitted[horizon] = clf
            counts[horizon] = int(len(yh))
            means[horizon] = mu
            scales[horizon] = sd
        self.models_, self.n_train_ = fitted, counts
        self.scaler_mean_, self.scaler_scale_ = means, scales
        self.feature_columns_ = list(frame.columns)
        return self

    def predict_direction_proba(
        self,
        latest_features: pd.DataFrame,
        as_of: str | None = None,
        data_version: str = "unspecified",
    ) -> dict[int, ForecastResult]:
        """Direction probabilities for the latest feature row, per horizon."""
        frame = pd.DataFrame(latest_features)
        if len(frame) == 0:
            raise ValueError("features frame is empty")
        missing = [c for c in self.feature_columns_ if c not in frame.columns]
        if missing:
            raise ValueError(f"latest_features missing columns: {missing}")
        base = frame.iloc[[-1]][self.feature_columns_].to_numpy(dtype=float)
        out: dict[int, ForecastResult] = {}
        for horizon, clf in self.models_.items():
            mu = self.scaler_mean_.get(horizon)
            sd = self.scaler_scale_.get(horizon)
            if mu is None or sd is None:
                row = base
            else:
                row = (base - np.asarray(mu)) / np.asarray(sd)
            proba = float(clf.predict_proba(row)[0, 1])
            out[horizon] = ForecastResult(
                TARGET_DIRECTION, horizon, min(max(proba, 0.0), 1.0),
                FORMULA, MODEL_NAME, MODEL_VERSION, FEATURE_VERSION,
                data_version, as_of,
            )
        return out

    def predict_proba_batch(
        self,
        features_frame: pd.DataFrame,
        as_of: str | None = None,
        data_version: str = "unspecified",
    ) -> dict[int, np.ndarray]:
        """Batched P(up) arrays for every row of ``features_frame``.

        Single ``predict_proba`` call per horizon (no per-row Python loop).
        Applies the same train-only per-horizon standardization as
        :meth:`predict_direction_proba`. Returns ``{horizon:
        np.ndarray[float]}`` clipped to [0, 1].
        """
        if not self.models_:
            raise ValueError("model is not fitted; call fit() first")
        frame = pd.DataFrame(features_frame)
        missing = [c for c in self.feature_columns_ if c not in frame.columns]
        if missing:
            raise ValueError(f"latest_features missing columns: {missing}")
        if len(frame) == 0:
            raise ValueError("features frame is empty")
        X = frame[self.feature_columns_].to_numpy(dtype=float)
        out: dict[int, np.ndarray] = {}
        for horizon, clf in self.models_.items():
            mu = self.scaler_mean_.get(horizon)
            sd = self.scaler_scale_.get(horizon)
            if mu is None or sd is None:
                Xs = X
            else:
                Xs = (X - np.asarray(mu)) / np.asarray(sd)
            proba = np.asarray(clf.predict_proba(Xs)[:, 1], dtype=float)
            out[horizon] = np.clip(proba, 0.0, 1.0)
        return out

    def predict_proba_values(
        self,
        features_frame: pd.DataFrame,
    ) -> dict[int, list[float]]:
        """Plain-list view of :meth:`predict_proba_batch` (JSON-friendly)."""
        return {h: [float(v) for v in arr] for h, arr in self.predict_proba_batch(features_frame).items()}


__all__ = ["LogisticDirectionModel", "MODEL_NAME", "MODEL_VERSION"]
