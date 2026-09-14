"""Baseline 4: gradient-boosted direction STUB (Milestone 3).

Minimal GradientBoostingClassifier wrapper (50 shallow trees, fixed
random_state=0, subsample=1.0 so fits are deterministic). Intentionally
untuned: a reference nonlinear baseline, NOT a production model. Same
fit/predict interface and label discipline as the logistic baseline.
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
from ..features.features import FEATURE_VERSION, direction_label

MODEL_NAME = "gradient-boost-direction"
MODEL_VERSION = "gradient-boost-direction-v1-stub"
FORMULA = (
    "STUB: P(up_h) from GradientBoostingClassifier(n_estimators=50, "
    "max_depth=2, learning_rate=0.1, subsample=1.0, random_state=0) "
    "per horizon on v1 features"
)
MIN_SAMPLES = 20


class GradientBoostDirectionModel:
    """Untuned gradient-boosting direction classifier per horizon (stub)."""

    def __init__(self, horizons: Sequence[int] = FORECAST_HORIZONS) -> None:
        self.horizons = tuple(int(h) for h in horizons)
        if any(h < 1 for h in self.horizons):
            raise ValueError("horizons must be >= 1")
        self.models_: dict[int, Any] = {}
        self.n_train_: dict[int, int] = {}
        self.feature_columns_: list[str] = []

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
            clf = GradientBoostingClassifier(
                n_estimators=50, max_depth=2, learning_rate=0.1,
                subsample=1.0, random_state=0)
            clf.fit(Xh, yh)
            fitted[horizon] = clf
            counts[horizon] = int(len(yh))
        self.models_, self.n_train_ = fitted, counts
        self.feature_columns_ = list(frame.columns)
        return self

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
        row = frame.iloc[[-1]][self.feature_columns_].to_numpy(dtype=float)
        out: dict[int, ForecastResult] = {}
        for horizon, clf in self.models_.items():
            proba = float(clf.predict_proba(row)[0, 1])
            out[horizon] = ForecastResult(
                TARGET_DIRECTION, horizon, min(max(proba, 0.0), 1.0),
                FORMULA, MODEL_NAME, MODEL_VERSION, FEATURE_VERSION,
                data_version, as_of,
            )
        return out


__all__ = ["GradientBoostDirectionModel", "MODEL_NAME", "MODEL_VERSION"]
