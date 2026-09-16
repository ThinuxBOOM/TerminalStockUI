"""Baseline 4: gradient-boosted direction model (v1, ensemble-v2).

GradientBoostingClassifier wrapper (50 shallow trees, fixed random_state=0,
subsample=1.0 so fits are deterministic). Promoted from stub in ensemble-v2:
now trained on the v2 extended feature frame with the same train-only
per-horizon standardization as the logistic baseline (trees don't need it,
but it keeps predict paths identical and guards future scale-sensitive
upgrades), plus a batched predict path for walk-forward/backtest parity.

Same fit/predict interface and label discipline as the logistic baseline.
Deterministic throughout.
"""

from __future__ import annotations

from typing import Any, Sequence

import numpy as np
import pandas as pd

try:
    from sklearn.ensemble import GradientBoostingClassifier
except ImportError:  # pragma: no cover - optional dep; degraded at fit time
    GradientBoostingClassifier = None  # type: ignore[assignment]


def _require_sklearn() -> None:
    if GradientBoostingClassifier is None:
        raise ImportError(
            "scikit-learn is not installed; GradientBoostDirectionModel.fit() "
            "is unavailable. Install scikit-learn or rely on the "
            "drift+momentum ensemble fallback."
        )

from ..common import FORECAST_HORIZONS, TARGET_DIRECTION, ForecastResult
from ..features.features import EXTENDED_FEATURE_VERSION, direction_label

MODEL_NAME = "gradient-boost-direction"
MODEL_VERSION = "gradient-boost-direction-v1"
FORMULA = (
    "P(up_h) from GradientBoostingClassifier(n_estimators=50, "
    "max_depth=2, learning_rate=0.1, subsample=1.0, random_state=0) "
    "per horizon on v2 extended features (train-only standardization)"
)
MIN_SAMPLES = 20


class GradientBoostDirectionModel:
    """Gradient-boosting direction classifier per horizon (ensemble-v2 member)."""

    def __init__(self, horizons: Sequence[int] = FORECAST_HORIZONS) -> None:
        self.horizons = tuple(int(h) for h in horizons)
        if any(h < 1 for h in self.horizons):
            raise ValueError("horizons must be >= 1")
        self.models_: dict[int, Any] = {}
        self.n_train_: dict[int, int] = {}
        self.feature_columns_: list[str] = []
        # Train-only standardization per horizon (mirrors logistic v3 so
        # both ML members share identical predict preprocessing).
        self.scaler_mean_: dict[int, Any] = {}
        self.scaler_scale_: dict[int, Any] = {}

    def fit(self, features: pd.DataFrame, close: pd.Series) -> "GradientBoostDirectionModel":
        """Fit one classifier per horizon on label-observable rows."""
        _require_sklearn()
        frame = pd.DataFrame(features)
        if len(frame) == 0:
            raise ValueError("feature frame is empty")
        both = frame.join(pd.Series(close, dtype=float).rename("__close__"), how="inner")
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
            mu = Xh.mean(axis=0)
            sd = Xh.std(axis=0, ddof=0)
            sd = np.where(np.isfinite(sd) & (sd > 1e-12), sd, 1.0)
            Xh_s = (Xh - mu) / sd
            clf = GradientBoostingClassifier(
                n_estimators=50, max_depth=2, learning_rate=0.1,
                subsample=1.0, random_state=0)
            clf.fit(Xh_s, yh)
            fitted[horizon] = clf
            counts[horizon] = int(len(yh))
            means[horizon] = mu
            scales[horizon] = sd
        self.models_, self.n_train_ = fitted, counts
        self.scaler_mean_, self.scaler_scale_ = means, scales
        self.feature_columns_ = list(frame.columns)
        return self

    def _standardize(self, X: np.ndarray, horizon: int) -> np.ndarray:
        mu = self.scaler_mean_.get(horizon)
        sd = self.scaler_scale_.get(horizon)
        if mu is None or sd is None:
            return X
        return (X - np.asarray(mu)) / np.asarray(sd)

    def predict_direction_proba(
        self,
        latest_features: pd.DataFrame,
        as_of: str | None = None,
        data_version: str = "unspecified",
    ) -> dict[int, ForecastResult]:
        """Direction probabilities for the latest feature row, per horizon."""
        if not self.models_:
            raise ValueError("model is not fitted; call fit() first")
        frame = pd.DataFrame(latest_features)
        missing = [c for c in self.feature_columns_ if c not in frame.columns]
        if missing:
            raise ValueError(f"latest_features missing columns: {missing}")
        base = frame.iloc[[-1]][self.feature_columns_].to_numpy(dtype=float)
        out: dict[int, ForecastResult] = {}
        for horizon, clf in self.models_.items():
            row = self._standardize(base, horizon)
            proba = float(clf.predict_proba(row)[0, 1])
            out[horizon] = ForecastResult(
                TARGET_DIRECTION, horizon, min(max(proba, 0.0), 1.0),
                FORMULA, MODEL_NAME, MODEL_VERSION, EXTENDED_FEATURE_VERSION,
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

        Single ``predict_proba`` call per horizon with the same train-only
        per-horizon standardization as :meth:`predict_direction_proba`.
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
            Xs = self._standardize(X, horizon)
            proba = np.asarray(clf.predict_proba(Xs)[:, 1], dtype=float)
            out[horizon] = np.clip(proba, 0.0, 1.0)
        return out


__all__ = ["GradientBoostDirectionModel", "MODEL_NAME", "MODEL_VERSION"]
