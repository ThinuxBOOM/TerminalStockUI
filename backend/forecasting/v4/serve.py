"""Serve v4 forecasts: one symbol on demand, or the whole universe daily.

No model fitting happens here: features from the latest bars, a few dot
products, and quantile lookups. A forecast for one symbol takes
milliseconds once its bars are loaded.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from . import features as F
from .model import CS_FEATURES, Bundle, CrossSection

US_MICS = {"XNYS", "XNAS"}

#: Plain-English names for the features behind a forecast's reasons.
FEATURE_LABELS = {
    "mom_5": "1-week return", "mom_21": "1-month return", "mom_63": "3-month return",
    "mom_126": "6-month return", "mom_12_1": "12-month momentum (excluding the last month)",
    "rev_1": "yesterday's move", "dist_sma50": "distance from the 50-day average",
    "dist_sma200": "distance from the 200-day average", "dd_252": "distance from the 52-week high",
    "rsi_14": "14-day RSI", "vol_ratio_21_252": "recent vs. 1-year volatility",
    "vol_ratio_63_252": "3-month vs. 1-year volatility", "log_vol_63": "volatility level",
    "abn_volume": "trading volume vs. normal", "range_ratio": "daily range vs. normal",
    "rel_mom_63": "3-month return vs. the market",
    "sec_mom_21": "1-month return vs. its sector", "sec_mom_63": "3-month return vs. its sector",
    "sec_mom_12_1": "12-month momentum vs. its sector",
}


def _variance_inputs(frame: pd.DataFrame) -> pd.DataFrame:
    r = np.log(frame["close"].astype(float)).diff()
    return pd.DataFrame({f"v{w}": r.rolling(w, min_periods=int(w * 0.8)).var() for w in (5, 21, 63, 252)})


def regime_of(vol_ratio: float | None) -> str:
    """Volatility regime from log(21-day vol / 1-year vol)."""
    if vol_ratio is None or not math.isfinite(vol_ratio):
        return "normal"
    if vol_ratio > 0.25:
        return "high"
    if vol_ratio < -0.25:
        return "low"
    return "normal"


def _f(x) -> float | None:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _drivers(bundle: Bundle, horizon: int, X: pd.DataFrame, cs_row: pd.Series, k: int = 3) -> dict:
    """Largest positive and negative contributions to the outperformance score."""
    contrib = bundle.out[str(horizon)].contributions(X).iloc[0]
    items = []
    for feat, c in contrib.items():
        base = feat.replace("cs_sec_", "sec_").replace("cs_", "")
        pct = cs_row.get(feat)
        items.append({
            "feature": base, "label": FEATURE_LABELS.get(base, base),
            "contribution": round(float(c), 4),
            "percentile": round(float(pct) + 0.5, 3) if pct is not None and math.isfinite(pct) else None,
        })
    items.sort(key=lambda d: d["contribution"])
    pos = [d for d in reversed(items) if d["contribution"] > 0][:k]
    neg = [d for d in items if d["contribution"] < 0][:k]
    return {"for": pos, "against": neg}


def forecast_frame(
    frame: pd.DataFrame, market: pd.DataFrame | None, bundle: Bundle, cs: CrossSection | None,
    *, sector: str | None, mic: str, horizons: list[int] | None = None,
) -> dict[int, dict]:
    """Forecasts for one symbol from its daily frame (index: session days)."""
    if len(frame) < F.WARMUP:
        raise ValueError(f"need {F.WARMUP} daily bars for a forecast, have {len(frame)}")
    feats = F.build(frame, market)
    var_in = _variance_inputs(frame)
    last = feats.index[-1]
    row = feats.iloc[-1]
    X_vol = var_in.iloc[[-1]]
    if X_vol.isna().any(axis=1).iloc[0] or (X_vol <= 0).any(axis=1).iloc[0]:
        raise ValueError("not enough clean history to estimate volatility")
    relative_ok = cs is not None and mic in US_MICS
    cs_row = cs.transform(row, sector) if cs is not None else pd.Series(0.0, index=CS_FEATURES)
    X = pd.DataFrame([cs_row[CS_FEATURES].to_numpy()], columns=CS_FEATURES, index=[last])
    close = float(frame["close"].iloc[-1])
    regime = regime_of(_f(row.get("vol_ratio_21_252")))
    out: dict[int, dict] = {}
    for h in horizons or bundle.horizons:
        vm = bundle.vol[str(h)]
        sigma = float(vm.sigma(X_vol)[0])
        q_log = {q: float(v[0]) for q, v in vm.quantiles(X_vol).items()}
        up, rel = bundle.up[str(h)], bundle.out[str(h)]
        p_out_raw = float(rel.raw(X)[0])
        res = {
            "horizon_days": h,
            "as_of": str(pd.Timestamp(last).date()),
            "last_close": close,
            "sigma": sigma,
            "vol_annual_forecast": sigma / math.sqrt(h) * math.sqrt(252),
            "quantiles": {q: math.expm1(v) for q, v in q_log.items()},
            "price_quantiles": {q: close * math.exp(v) for q, v in q_log.items()},
            "drawdown_prob": float(vm.drawdown_prob(X_vol)[0]),
            "drawdown_threshold": 0.10,
            "regime": regime,
            "base_up": up.base_rate,
            "p_up": float(up.predict(X)[0]),
            "p_up_raw": float(up.raw(X)[0]),
            "p_out": float(rel.predict(X)[0]) if relative_ok else None,
            "p_out_raw": p_out_raw if relative_ok else None,
            "out_rank": cs.score_rank(h, p_out_raw) if relative_ok and cs is not None else None,
            "drivers": _drivers(bundle, h, X, cs_row) if relative_ok else {"for": [], "against": []},
            "relative_available": relative_ok,
            "cross_section_as_of": cs.as_of if cs is not None else None,
            "features": {k: _f(v) for k, v in row.items()},
        }
        out[h] = res
    return out


def build_cross_section(
    frames: dict[str, pd.DataFrame], market: pd.DataFrame | None, bundle: Bundle,
) -> tuple[CrossSection, dict[str, pd.Series]]:
    """Today's universe distribution (grids, sector medians, score grids)."""
    latest: dict[str, pd.Series] = {}
    for sym, frame in frames.items():
        if len(frame) < F.WARMUP:
            continue
        feats = F.build(frame, market)
        latest[sym] = feats.iloc[-1]
    if len(latest) < 50:
        raise ValueError(f"cross-section needs at least 50 symbols, have {len(latest)}")
    table = pd.DataFrame(latest).T
    days = {str(pd.Timestamp(frames[s].index[-1]).date()) for s in latest}
    as_of = max(days)
    cs = CrossSection.from_features(table, bundle.sectors, as_of)
    X = pd.DataFrame([cs.transform(table.loc[s], bundle.sectors.get(s))[CS_FEATURES] for s in table.index],
                     index=table.index)
    q = np.linspace(0, 1, 101)
    for h in bundle.horizons:
        raw = bundle.out[str(h)].raw(X)
        cs.score_grids[str(h)] = [float(v) for v in np.quantile(raw, q)]
    return cs, latest
