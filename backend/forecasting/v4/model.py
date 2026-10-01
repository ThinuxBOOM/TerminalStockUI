"""v4 model bundle: JSON-serializable coefficients, no pickles.

* :class:`LogitModel` — imputation medians, standardization and logistic
  coefficients exported from scikit-learn, evaluated with numpy.
* Cross-sectional features — each stock feature becomes its percentile among
  the universe on that date (minus 0.5), plus momentum relative to the
  stock's sector. At serving time the day's cross-section is a quantile grid
  per feature (:class:`CrossSection`) built by the daily scoring job.
* :class:`Bundle` — everything the app needs to forecast, plus the
  walk-forward report that becomes the "measured" record in the UI.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from . import features as F
from .volatility import VolModel

ENGINE = "v4"
#: Stock features ranked across the universe each day.
CS_BASE = F.STOCK_FEATURES + ["rel_mom_63"]
#: Momentum measured against the stock's sector median, then ranked.
SECTOR_BASE = ("mom_21", "mom_63", "mom_12_1")
CS_FEATURES = [f"cs_{c}" for c in CS_BASE] + [f"cs_sec_{c}" for c in SECTOR_BASE]
GRID_POINTS = 101


# --- cross-sectional transform ----------------------------------------------

def cs_transform_panel(panel: pd.DataFrame, sectors: dict[str, str]) -> pd.DataFrame:
    """Training-time transform: exact per-date percentile ranks."""
    sec = pd.Series(panel.index.get_level_values("symbol")).map(sectors).fillna("Other").to_numpy()
    out = pd.DataFrame(index=panel.index)
    by_date = panel.groupby(level="date")
    for c in CS_BASE:
        out[f"cs_{c}"] = (by_date[c].rank(pct=True) - 0.5).astype("float32")
    keys = [panel.index.get_level_values("date"), sec]
    for c in SECTOR_BASE:
        rel = panel[c] - panel[c].groupby(keys).transform("median")
        out[f"cs_sec_{c}"] = (rel.groupby(level="date").rank(pct=True) - 0.5).astype("float32")
    return out


@dataclass
class CrossSection:
    """One date's universe distribution: quantile grids and sector medians."""

    as_of: str
    grids: dict[str, list[float]] = field(default_factory=dict)
    sector_medians: dict[str, dict[str, float]] = field(default_factory=dict)
    size: int = 0
    #: Per horizon: quantile grid of the raw outperformance score across the
    #: universe that day, so one symbol's score becomes a percentile rank.
    score_grids: dict[str, list[float]] = field(default_factory=dict)

    def score_rank(self, horizon: int, raw: float) -> float | None:
        grid = self.score_grids.get(str(horizon))
        if not grid or not np.isfinite(raw):
            return None
        return float(np.clip(np.interp(raw, np.asarray(grid), np.linspace(0, 1, len(grid))), 0, 1))

    @classmethod
    def from_features(cls, feats: pd.DataFrame, sectors: dict[str, str], as_of: str) -> "CrossSection":
        """``feats``: one row per symbol (index: symbol) with raw FEATURES."""
        q = np.linspace(0, 1, GRID_POINTS)
        grids = {}
        for c in CS_BASE:
            col = feats[c].dropna().to_numpy(dtype="float64")
            if len(col) >= 20:
                grids[c] = [float(v) for v in np.quantile(col, q)]
        sec = feats.index.map(lambda s: sectors.get(s, "Other"))
        medians: dict[str, dict[str, float]] = {}
        for c in SECTOR_BASE:
            for name, val in feats[c].groupby(sec).median().items():
                medians.setdefault(str(name), {})[c] = float(val)
        rel_grids = {}
        for c in SECTOR_BASE:
            rel = feats[c] - sec.map(lambda s, c=c: medians.get(s, {}).get(c, np.nan)).to_numpy()
            rel = rel.dropna().to_numpy(dtype="float64")
            if len(rel) >= 20:
                rel_grids[f"sec_{c}"] = [float(v) for v in np.quantile(rel, q)]
        return cls(as_of, {**grids, **rel_grids}, medians, int(len(feats)))

    def _rank(self, name: str, x: float | None) -> float:
        grid = self.grids.get(name)
        if grid is None or x is None or not np.isfinite(x):
            return 0.0  # median when unknown
        g = np.asarray(grid)
        # Ties at the ends of a grid map to the middle of the tied span.
        lo = np.searchsorted(g, x, side="left")
        hi = np.searchsorted(g, x, side="right")
        pos = (lo + hi) / 2 / (len(g) - 1) if hi > lo else np.interp(x, g, np.linspace(0, 1, len(g)))
        return float(np.clip(pos, 0, 1) - 0.5)

    def transform(self, row: pd.Series, sector: str | None) -> pd.Series:
        """Raw FEATURES for one symbol -> CS_FEATURES."""
        out = {f"cs_{c}": self._rank(c, row.get(c)) for c in CS_BASE}
        meds = self.sector_medians.get(sector or "", {})
        for c in SECTOR_BASE:
            x = row.get(c)
            m = meds.get(c)
            rel = (x - m) if (x is not None and m is not None and np.isfinite(x)) else None
            out[f"cs_sec_{c}"] = self._rank(f"sec_{c}", rel) if rel is not None else 0.0
        return pd.Series(out)


# --- logistic model ------------------------------------------------------------

@dataclass
class LogitModel:
    features: list[str]
    medians: list[float]
    means: list[float]
    scales: list[float]
    coef: list[float]
    intercept: float
    base_rate: float
    #: Shipped probability = (1 - blend) * base_rate + blend * raw model.
    blend: float = 1.0

    @classmethod
    def fit(cls, X: pd.DataFrame, y: pd.Series, *, C: float = 0.01) -> "LogitModel":
        from sklearn.linear_model import LogisticRegression

        x = X.to_numpy(dtype="float64")
        med = np.nanmedian(x, axis=0)
        med = np.where(np.isfinite(med), med, 0.0)
        x = np.where(np.isfinite(x), x, med)
        mean, scale = x.mean(axis=0), x.std(axis=0)
        scale = np.where(scale > 0, scale, 1.0)
        lr = LogisticRegression(C=C, max_iter=500).fit((x - mean) / scale, y.astype(int).to_numpy())
        return cls(list(X.columns), med.tolist(), mean.tolist(), scale.tolist(),
                   lr.coef_[0].tolist(), float(lr.intercept_[0]), float(y.mean()))

    def _z(self, X: pd.DataFrame) -> np.ndarray:
        x = X[self.features].to_numpy(dtype="float64")
        x = np.where(np.isfinite(x), x, np.asarray(self.medians))
        return (x - np.asarray(self.means)) / np.asarray(self.scales)

    def raw(self, X: pd.DataFrame) -> np.ndarray:
        return 1.0 / (1.0 + np.exp(-(self._z(X) @ np.asarray(self.coef) + self.intercept)))

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return (1 - self.blend) * self.base_rate + self.blend * self.raw(X)

    def contributions(self, X: pd.DataFrame) -> pd.DataFrame:
        """Per-feature log-odds contributions (coef x standardized value)."""
        return pd.DataFrame(self._z(X) * np.asarray(self.coef), index=X.index, columns=self.features)

    # sklearn-style shim for evaluate.walk_forward
    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        p = self.raw(X)
        return np.column_stack([1 - p, p])


class LogitFactory:
    """walk_forward() expects factory().fit(X, y) -> model with predict_proba."""

    def __init__(self, C: float = 0.01):
        self.C = C

    def __call__(self) -> "_Fitter":
        return _Fitter(self.C)


class _Fitter:
    def __init__(self, C: float):
        self.C = C
        self.model: LogitModel | None = None

    def fit(self, X, y):
        self.model = LogitModel.fit(X, y, C=self.C)
        return self

    def predict_proba(self, X):
        assert self.model is not None
        return self.model.predict_proba(X)


# --- bundle --------------------------------------------------------------------

@dataclass
class Bundle:
    version: str
    trained_at: str
    data_start: str
    data_end: str
    universe: list[str]
    sectors: dict[str, str]
    horizons: list[int]
    vol: dict[str, VolModel]
    up: dict[str, LogitModel]
    out: dict[str, LogitModel]
    report: dict = field(default_factory=dict)
    #: Cross-section at the end of the training data (fallback until the
    #: daily scoring job has produced a fresher one).
    cross_section: CrossSection | None = None

    def to_json(self) -> str:
        d = asdict(self)
        d["vol"] = {h: m.to_dict() for h, m in self.vol.items()}
        d["engine"] = ENGINE
        return json.dumps(d, separators=(",", ":"))

    @classmethod
    def from_json(cls, text: str | bytes | dict) -> "Bundle":
        d = json.loads(text) if not isinstance(text, dict) else text
        cs = d.get("cross_section")
        return cls(
            version=d["version"], trained_at=d["trained_at"], data_start=d["data_start"],
            data_end=d["data_end"], universe=list(d["universe"]), sectors=dict(d["sectors"]),
            horizons=[int(h) for h in d["horizons"]],
            vol={str(h): VolModel.from_dict(m) for h, m in d["vol"].items()},
            up={str(h): LogitModel(**m) for h, m in d["up"].items()},
            out={str(h): LogitModel(**m) for h, m in d["out"].items()},
            report=d.get("report") or {},
            cross_section=CrossSection(**cs) if cs else None,
        )
