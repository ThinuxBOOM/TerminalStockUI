"""Walk-forward evaluation by calendar year, pooled across symbols.

Each test year is scored by a model trained only on earlier dates, with an
embargo of ``horizon`` sessions so no training label overlaps the test
period. Points on the same date are correlated, so confidence intervals use
a block bootstrap over calendar months, not over rows.
"""

from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd

from . import features as F
from . import volatility as V


def year_folds(dates: pd.DatetimeIndex, first_test_year: int, horizon: int):
    """Yield (test_year, train_end, test_start, test_end) with an h-session embargo."""
    uniq = pd.DatetimeIndex(sorted(set(dates)))
    for year in range(first_test_year, uniq.max().year + 1):
        test = uniq[(uniq.year == year)]
        if len(test) < 20:
            continue
        start_pos = uniq.get_loc(test[0])
        if start_pos - horizon - 1 < 0:
            continue
        yield year, uniq[start_pos - horizon - 1], test[0], test[-1]


def _dates(panel: pd.DataFrame) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(panel.index.get_level_values("date"))


def subsample_dates(df: pd.DataFrame, step: int) -> pd.DataFrame:
    if step <= 1:
        return df
    d = _dates(df)
    uniq = pd.DatetimeIndex(sorted(set(d)))
    keep = set(uniq[::step])
    return df[d.isin(keep)]


def walk_forward(
    panel: pd.DataFrame, horizon: int, label: str, factory: Callable[[], object],
    *, first_test_year: int = 2021, step: int = 5, features: list[str] | None = None,
) -> pd.DataFrame:
    """Out-of-fold probabilities for ``label`` (e.g. up_21); returns p, y, base, fwd."""
    feats = features or F.FEATURES
    data = panel[feats + [label, f"fwd_{horizon}"]].dropna(subset=[label])
    dates = _dates(data)
    out = []
    for _year, train_end, test_start, test_end in year_folds(dates, first_test_year, horizon):
        train = subsample_dates(data[dates <= train_end], step)
        test = data[(dates >= test_start) & (dates <= test_end)]
        if len(train) < 1000 or len(test) == 0:
            continue
        model = factory()
        model.fit(train[feats], train[label].astype(int))
        p = model.predict_proba(test[feats])[:, 1]
        out.append(pd.DataFrame({
            "p": p, "y": test[label].to_numpy(), "base": float(train[label].mean()),
            "fwd": test[f"fwd_{horizon}"].to_numpy(),
        }, index=test.index))
    return pd.concat(out) if out else pd.DataFrame(columns=["p", "y", "base", "fwd"])


def _block_bootstrap(df: pd.DataFrame, stat: Callable[[pd.DataFrame], float], n_boot: int, seed: int) -> list[float]:
    months = _dates(df).to_period("M")
    groups = {m: idx for m, idx in df.groupby(months).indices.items()}
    keys = list(groups)
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(n_boot):
        pick = rng.integers(0, len(keys), len(keys))
        idx = np.concatenate([groups[keys[i]] for i in pick])
        vals.append(stat(df.iloc[idx]))
    return [float(np.quantile(vals, 0.025)), float(np.quantile(vals, 0.975))]


def brier_skill(oof: pd.DataFrame, *, n_boot: int = 300, seed: int = 7) -> dict:
    """Brier of the model vs the training base rate, with a month-block bootstrap CI."""
    def skill(d: pd.DataFrame) -> float:
        bm = float(np.mean((d["p"] - d["y"]) ** 2))
        bb = float(np.mean((d["base"] - d["y"]) ** 2))
        return 1 - bm / bb

    bm = float(np.mean((oof["p"] - oof["y"]) ** 2))
    bb = float(np.mean((oof["base"] - oof["y"]) ** 2))
    return {
        "points": int(len(oof)),
        "dates": int(_dates(oof).nunique()),
        "up_share": round(float(oof["y"].mean()), 4),
        "brier_model": round(bm, 5),
        "brier_base": round(bb, 5),
        "skill": round(1 - bm / bb, 4),
        "skill_ci95": [round(v, 4) for v in _block_bootstrap(oof, skill, n_boot, seed)],
        "hit_rate": round(float(np.mean((oof["p"] > 0.5) == (oof["y"] == 1))), 4),
        "mean_p": round(float(oof["p"].mean()), 4),
        "p_std": round(float(oof["p"].std()), 4),
    }


def rank_ic(oof: pd.DataFrame, horizon: int) -> dict:
    """Cross-sectional Spearman IC of p vs forward return, per date.

    The t-stat uses every h-th date so overlapping forward returns are not
    double-counted. Also reports the mean top-minus-bottom decile return.
    """
    def per_date(g: pd.DataFrame) -> pd.Series:
        if len(g) < 30:
            return pd.Series({"ic": np.nan, "spread": np.nan})
        ic = g["p"].rank().corr(g["fwd"].rank())
        q = g["p"].rank(pct=True)
        spread = g.loc[q > 0.9, "fwd"].mean() - g.loc[q <= 0.1, "fwd"].mean()
        return pd.Series({"ic": ic, "spread": spread})

    by_date = oof.groupby(level="date").apply(per_date).dropna()
    nonoverlap = by_date.iloc[::max(1, horizon)]
    ic = nonoverlap["ic"]
    return {
        "ic_mean": round(float(by_date["ic"].mean()), 4),
        "ic_t": round(float(ic.mean() / (ic.std() / np.sqrt(len(ic)))), 2) if len(ic) > 2 else None,
        "decile_spread": round(float(by_date["spread"].mean()), 5),
        "ic_positive_share": round(float((by_date["ic"] > 0).mean()), 3),
    }


def per_year(oof: pd.DataFrame) -> dict:
    d = _dates(oof)
    out = {}
    for year, g in oof.groupby(d.year):
        bm = float(np.mean((g["p"] - g["y"]) ** 2))
        bb = float(np.mean((g["base"] - g["y"]) ** 2))
        out[str(year)] = round(1 - bm / bb, 4)
    return out


# --- Volatility / range ------------------------------------------------------

def walk_forward_vol(panel: pd.DataFrame, horizon: int, *, first_test_year: int = 2021, step: int = 5) -> pd.DataFrame:
    """OOF q10/q90, sigma and drop-risk for the log-HAR model."""
    cols = list(V.VAR_INPUTS) + [f"var_{horizon}", f"fwd_{horizon}", f"bigdd_{horizon}"]
    data = panel[cols].dropna(subset=list(V.VAR_INPUTS) + [f"fwd_{horizon}"])
    data = data[(data[list(V.VAR_INPUTS)] > 0).all(axis=1)]
    dates = _dates(data)
    out = []
    for _year, train_end, test_start, test_end in year_folds(dates, first_test_year, horizon):
        train = subsample_dates(data[dates <= train_end], step).dropna()
        test = data[(dates >= test_start) & (dates <= test_end)]
        model = V.fit(train, horizon)
        q = model.quantiles(test)
        out.append(pd.DataFrame({
            "lo": q["0.10"], "hi": q["0.90"], "sigma": model.sigma(test),
            "dd_p": model.drawdown_prob(test), "dd_base": float(train[f"bigdd_{horizon}"].mean()),
            "y": test[f"fwd_{horizon}"].to_numpy(), "dd_y": test[f"bigdd_{horizon}"].to_numpy(),
        }, index=test.index))
    return pd.concat(out)


def trailing_window_range(frames: dict[str, pd.DataFrame], horizon: int, window: int = 500) -> pd.DataFrame:
    """Baseline (the v3 method): per-symbol q10/q90 of trailing h-day returns, and
    trailing frequency of 10%+ drops."""
    parts = []
    for sym, frame in frames.items():
        logp = np.log(frame["close"].astype(float))
        hret = logp - logp.shift(horizon)
        lo = hret.rolling(window, min_periods=120).quantile(0.10)
        hi = hret.rolling(window, min_periods=120).quantile(0.90)
        dd = F.forward_max_drawdown(frame["close"], horizon).shift(horizon)  # known by t
        dd_freq = (dd <= np.log(0.9)).astype(float).where(dd.notna()).rolling(window, min_periods=120).mean()
        parts.append(pd.DataFrame({"lo_tw": lo, "hi_tw": hi, "dd_tw": dd_freq, "symbol": sym}, index=frame.index))
    df = pd.concat(parts)
    df.index.name = "date"
    return df.set_index("symbol", append=True)


def range_metrics(lo: np.ndarray, hi: np.ndarray, y: np.ndarray) -> dict:
    ok = np.isfinite(lo) & np.isfinite(hi) & np.isfinite(y)
    lo, hi, y = lo[ok], hi[ok], y[ok]
    inside = (y >= lo) & (y <= hi)
    return {
        "coverage_80": round(float(inside.mean()), 4),
        "below_q10": round(float((y < lo).mean()), 4),
        "above_q90": round(float((y > hi).mean()), 4),
        "mean_width": round(float(np.mean(hi - lo)), 5),
        "interval_score": round(float(np.mean(V.interval_score(lo, hi, y))), 5),
        "n": int(ok.sum()),
    }
