"""Research harness for forecast engine v4 (not used by the app at runtime).

    python scripts/research_v4.py --cache-dir .research vol
    python scripts/research_v4.py --cache-dir .research direction --models logit hgb

Downloads ~10 years of S&P 500 daily bars (cached), builds the feature
panel (cached), and prints walk-forward results by calendar year.
"""

from __future__ import annotations

import argparse
import json
import logging
import pickle
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from backend.forecasting.v4 import evaluate as E  # noqa: E402
from backend.forecasting.v4 import features as F  # noqa: E402
from backend.forecasting.v4.data import load_or_download, us_universe  # noqa: E402
from backend.forecasting.v4.panel import HORIZONS, add_labels, build_panel  # noqa: E402


def load_panel(cache_dir: Path) -> tuple[pd.DataFrame, dict]:
    frames = load_or_download(us_universe() + ["SPY"], cache_dir / "us_10y.pkl", max_age_hours=24 * 30)
    market = frames.pop("SPY")
    path = cache_dir / "panel.pkl"
    if path.exists() and path.stat().st_mtime > (cache_dir / "us_10y.pkl").stat().st_mtime:
        with path.open("rb") as fh:
            return pickle.load(fh), frames
    today = pd.Timestamp.today().normalize()
    t = time.time()
    panel = add_labels(build_panel(frames, market, drop_after=today - pd.Timedelta(days=1)))
    print(f"panel {panel.shape} built in {time.time() - t:.0f}s", flush=True)
    with path.open("wb") as fh:
        pickle.dump(panel, fh)
    return panel, frames


def factories():
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    return {
        "logit": lambda: make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), LogisticRegression(C=0.01, max_iter=300)),
        "hgb": lambda: HistGradientBoostingClassifier(
            max_depth=3, learning_rate=0.05, max_iter=200, min_samples_leaf=2000,
            l2_regularization=1.0, random_state=0),
    }


def run_vol(panel: pd.DataFrame, frames: dict, horizons: list[int]) -> dict:
    report = {}
    for h in horizons:
        oof = E.walk_forward_vol(panel, h)
        tw = E.trailing_window_range(frames, h).reindex(oof.index)
        har = E.range_metrics(oof["lo"].to_numpy(), oof["hi"].to_numpy(), oof["y"].to_numpy())
        base = E.range_metrics(tw["lo_tw"].to_numpy(), tw["hi_tw"].to_numpy(), oof["y"].to_numpy())
        # Drop risk: HAR-logistic vs trailing per-symbol frequency vs pooled base rate.
        ok = oof["dd_y"].notna() & tw["dd_tw"].notna()
        y = oof.loc[ok, "dd_y"].to_numpy()
        b = {k: float(np.mean((v - y) ** 2)) for k, v in {
            "har": oof.loc[ok, "dd_p"].to_numpy(), "trailing": tw.loc[ok, "dd_tw"].to_numpy(),
            "base": oof.loc[ok, "dd_base"].to_numpy()}.items()}
        report[h] = {"har": har, "trailing_window": base,
                     "drop_risk_brier": {k: round(v, 5) for k, v in b.items()},
                     "drop_risk_skill_vs_base": round(1 - b["har"] / b["base"], 4),
                     "drop_rate": round(float(y.mean()), 4)}
        print(f"h={h:>2} {json.dumps(report[h])}", flush=True)
    return report


def cross_sectional(panel: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Per-date ranks of stock features in [-0.5, 0.5] plus sector-relative momentum."""
    from backend.forecasting.v4.data import sectors

    t = time.time()
    sec = pd.Series(panel.index.get_level_values("symbol")).map(sectors()).fillna("Other").to_numpy()
    out = panel.copy()
    g = out.groupby(level="date")
    cols = []
    for c in F.STOCK_FEATURES + ["rel_mom_63"]:
        out[f"cs_{c}"] = (g[c].rank(pct=True) - 0.5).astype("float32")
        cols.append(f"cs_{c}")
    keys = [out.index.get_level_values("date"), sec]
    for c in ("mom_21", "mom_63", "mom_12_1"):
        med = out[c].groupby(keys).transform("median")
        out[f"sec_{c}"] = (out[c] - med).astype("float32")
        out[f"cs_sec_{c}"] = (out.groupby(level="date")[f"sec_{c}"].rank(pct=True) - 0.5).astype("float32")
        cols.append(f"cs_sec_{c}")
    print(f"cross-sectional features in {time.time() - t:.0f}s", flush=True)
    return out, cols


def run_direction(panel: pd.DataFrame, horizons: list[int], models: list[str], targets: list[str],
                  feature_set: str = "raw") -> dict:
    facs = factories()
    report: dict = {}
    feats = None
    if feature_set in ("cs", "cs+mkt"):
        panel, feats = cross_sectional(panel)
        if feature_set == "cs+mkt":
            feats = feats + F.MARKET_FEATURES
    for h in horizons:
        for target in targets:
            label = f"{target}_{h}"
            for name in models:
                t = time.time()
                oof = E.walk_forward(panel, h, label, facs[name], step=2 if h == 1 else 5, features=feats)
                res = E.brier_skill(oof)
                res["per_year_skill"] = E.per_year(oof)
                res.update(E.rank_ic(oof, h))
                report[f"{label}:{name}"] = res
                print(f"{label:>7} {name:>5} ({time.time() - t:.0f}s) {json.dumps(res)}", flush=True)
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache-dir", type=Path, default=ROOT / ".research")
    ap.add_argument("what", choices=["vol", "direction", "panel"])
    ap.add_argument("--horizons", type=int, nargs="+", default=list(HORIZONS))
    ap.add_argument("--models", nargs="+", default=["logit", "hgb"])
    ap.add_argument("--targets", nargs="+", default=["up", "out"])
    ap.add_argument("--out", type=Path)
    ap.add_argument("--features", choices=["raw", "cs", "cs+mkt"], default="raw")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)
    panel, frames = load_panel(args.cache_dir)
    print(f"panel {panel.shape}, {panel.index.get_level_values('date').min().date()} .. "
          f"{panel.index.get_level_values('date').max().date()}", flush=True)
    if args.what == "vol":
        report = run_vol(panel, frames, args.horizons)
    elif args.what == "direction":
        report = run_direction(panel, args.horizons, args.models, args.targets, args.features)
    else:
        print(panel[F.FEATURES].describe().T.round(3).to_string())
        return 0
    if args.out:
        args.out.write_text(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
