"""Train and evaluate a v4 bundle from a dict of daily OHLCV frames.

Walk-forward by calendar year (see :mod:`.evaluate`) produces the measured
record; final models are then refit on all data. The blend between the base
rate and the model is chosen *nested*: each test year uses a blend learned
on earlier years only, so the reported skill is not tuned on itself.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import evaluate as E
from . import features as F
from . import volatility as V
from .model import CS_FEATURES, Bundle, CrossSection, LogitFactory, LogitModel, cs_transform_panel
from .panel import HORIZONS, add_labels, build_panel

log = logging.getLogger(__name__)

BLEND_GRID = np.round(np.linspace(0, 1, 21), 2)
FIRST_TEST_YEAR = 2021
LOGIT_C = 0.01


def _best_blend(df: pd.DataFrame) -> float:
    if df.empty:
        return 0.0
    y, p, b = df["y"].to_numpy(), df["p"].to_numpy(), df["base"].to_numpy()
    scores = [np.mean(((1 - k) * b + k * p - y) ** 2) for k in BLEND_GRID]
    return float(BLEND_GRID[int(np.argmin(scores))])


def nested_blend(oof: pd.DataFrame) -> tuple[pd.DataFrame, float]:
    """Replace p with the nested-blended probability; return the final blend."""
    years = pd.DatetimeIndex(oof.index.get_level_values("date")).year
    out = oof.copy()
    for year in sorted(set(years)):
        k = _best_blend(oof[years < year])  # 0 (base rate) for the first year
        mask = years == year
        out.loc[mask, "p"] = (1 - k) * oof.loc[mask, "base"] + k * oof.loc[mask, "p"]
    return out, _best_blend(oof)


def decile_table(oof: pd.DataFrame) -> list[dict]:
    """Average forward return by predicted-probability decile (per date, then averaged)."""
    d = oof.dropna(subset=["fwd"]).copy()
    d["decile"] = d.groupby(level="date")["p"].rank(pct=True).mul(10).clip(upper=9.999).astype(int)
    by = d.groupby([d.index.get_level_values("date"), "decile"])["fwd"].mean().groupby("decile").mean()
    return [{"decile": int(k) + 1, "mean_fwd_log_return": round(float(v), 5)} for k, v in by.items()]


def reliability(oof: pd.DataFrame, bins: int = 10) -> list[dict]:
    q = pd.qcut(oof["p"], bins, duplicates="drop")
    g = oof.groupby(q, observed=True)
    return [{"mean_p": round(float(x["p"].mean()), 4), "observed": round(float(x["y"].mean()), 4), "n": int(len(x))}
            for _, x in g]


def per_year_ic(oof: pd.DataFrame) -> dict:
    d = oof.dropna(subset=["fwd"])
    ic = d.groupby(level="date").apply(lambda g: g["p"].rank().corr(g["fwd"].rank()) if len(g) > 30 else np.nan)
    ic.index = pd.DatetimeIndex(ic.index)
    return {str(y): round(float(v), 4) for y, v in ic.groupby(ic.index.year).mean().items()}


def train_bundle(
    frames: dict[str, pd.DataFrame], market: pd.DataFrame, sectors: dict[str, str],
    *, horizons: tuple[int, ...] = HORIZONS, as_of: pd.Timestamp | None = None, n_boot: int = 300,
) -> Bundle:
    t0 = time.time()
    cutoff = (as_of or pd.Timestamp.today().normalize()) - pd.Timedelta(days=1)  # drop today's partial bar
    panel = add_labels(build_panel(frames, market, horizons, drop_after=cutoff), horizons)
    cs = cs_transform_panel(panel, sectors)
    panel = panel.join(cs)
    dates = pd.DatetimeIndex(panel.index.get_level_values("date"))
    log.info("panel %s in %.0fs", panel.shape, time.time() - t0)

    report: dict = {"engine": "v4", "horizons": {}, "first_test_year": FIRST_TEST_YEAR,
                    "universe_size": int(panel.index.get_level_values("symbol").nunique())}
    vol_models: dict[str, V.VolModel] = {}
    up_models: dict[str, LogitModel] = {}
    out_models: dict[str, LogitModel] = {}
    per_symbol: dict[str, dict] = {}

    for h in horizons:
        hr: dict = {}
        # -- volatility / range / drop risk -----------------------------------
        voof = E.walk_forward_vol(panel, h, first_test_year=FIRST_TEST_YEAR)
        rng = E.range_metrics(voof["lo"].to_numpy(), voof["hi"].to_numpy(), voof["y"].to_numpy())
        ok = voof["dd_y"].notna()
        b_model = float(np.mean((voof.loc[ok, "dd_p"] - voof.loc[ok, "dd_y"]) ** 2))
        b_base = float(np.mean((voof.loc[ok, "dd_base"] - voof.loc[ok, "dd_y"]) ** 2))
        vyear = pd.DatetimeIndex(voof.index.get_level_values("date")).year
        inside = (voof["y"] >= voof["lo"]) & (voof["y"] <= voof["hi"])
        hr["range"] = {**rng, "coverage_by_year": {str(y): round(float(v), 4) for y, v in inside.groupby(vyear).mean().items()}}
        hr["drop_risk"] = {"brier_model": round(b_model, 5), "brier_base": round(b_base, 5),
                           "skill": round(1 - b_model / b_base, 4) if b_base > 0 else None,
                           "event_rate": round(float(voof.loc[ok, "dd_y"].mean()), 4),
                           "threshold": 0.10}
        sym = voof.index.get_level_values("symbol")
        for s, cov in inside.groupby(sym).mean().items():
            per_symbol.setdefault(str(s), {}).setdefault(str(h), {})["range_coverage"] = round(float(cov), 3)
        vol_models[str(h)] = V.fit(E.subsample_dates(panel[dates <= cutoff], 5), h)

        # -- direction: absolute (up) and relative (out) ----------------------
        for target, store in (("up", up_models), ("out", out_models)):
            label = f"{target}_{h}"
            oof = E.walk_forward(panel, h, label, LogitFactory(LOGIT_C), first_test_year=FIRST_TEST_YEAR,
                                 step=2 if h == 1 else 5, features=CS_FEATURES)
            raw_skill = E.brier_skill(oof, n_boot=n_boot)
            blended, k = nested_blend(oof)
            res = E.brier_skill(blended, n_boot=n_boot)
            res.update(E.rank_ic(oof, h))
            res["raw_model_skill"] = raw_skill["skill"]
            res["blend"] = k
            res["per_year_skill"] = E.per_year(blended)
            res["per_year_ic"] = per_year_ic(oof)
            res["deciles"] = decile_table(oof)
            res["reliability"] = reliability(blended)
            hr[target] = res
            if target == "out":
                hit = ((oof["p"] > 0.5) == (oof["y"] == 1)).groupby(oof.index.get_level_values("symbol")).mean()
                for s, v in hit.items():
                    per_symbol.setdefault(str(s), {}).setdefault(str(h), {})["out_hit_rate"] = round(float(v), 3)
            train = E.subsample_dates(panel.dropna(subset=[label]), 2 if h == 1 else 5)
            model = LogitModel.fit(train[CS_FEATURES], train[label], C=LOGIT_C)
            model.blend = k
            store[str(h)] = model
            log.info("h=%d %s skill=%s ic=%s blend=%s", h, target, res["skill"], res["ic_mean"], k)
        report["horizons"][str(h)] = hr
    report["per_symbol"] = per_symbol

    last = dates.max()
    latest = panel.xs(last, level="date")[F.FEATURES]
    cross = CrossSection.from_features(latest, sectors, str(last.date()))
    latest_cs = panel.xs(last, level="date")[CS_FEATURES]
    for h in horizons:
        raw = out_models[str(h)].raw(latest_cs)
        cross.score_grids[str(h)] = [float(v) for v in np.quantile(raw, np.linspace(0, 1, 101))]
    now = datetime.now(timezone.utc)
    report["train_seconds"] = round(time.time() - t0, 1)
    return Bundle(
        version=f"v4-{now:%Y%m%d-%H%M}",
        trained_at=now.isoformat(timespec="seconds"),
        data_start=str(dates.min().date()),
        data_end=str(last.date()),
        universe=sorted(set(panel.index.get_level_values("symbol"))),
        sectors={k: v for k, v in sectors.items() if k in set(panel.index.get_level_values("symbol"))},
        horizons=list(horizons),
        vol=vol_models, up=up_models, out=out_models,
        report=report, cross_section=cross,
    )
