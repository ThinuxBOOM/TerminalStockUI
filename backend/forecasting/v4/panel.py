"""Panel dataset: features and labels for every (date, symbol)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import features as F

HORIZONS = (1, 7, 14, 21)
#: Drop larger than this within the horizon counts as a "large drop".
DRAWDOWN_THRESHOLD = 0.10


def build_panel(
    frames: dict[str, pd.DataFrame],
    market: pd.DataFrame | None,
    horizons: tuple[int, ...] = HORIZONS,
    *,
    drop_after: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Long frame indexed by (date, symbol): FEATURES, fwd_h, dd_h, var_h.

    ``drop_after`` removes bars after a date (e.g. today's unfinished session).
    """
    parts = []
    for sym, frame in frames.items():
        if drop_after is not None:
            frame = frame[frame.index <= drop_after]
        if len(frame) < F.WARMUP + max(horizons) + 5:
            continue
        feats = F.build(frame, market).astype("float32")
        close = frame["close"].astype(float)
        logret = np.log(close).diff()
        for h in horizons:
            feats[f"fwd_{h}"] = F.forward_log_return(close, h).astype("float32")
            feats[f"dd_{h}"] = F.forward_max_drawdown(close, h).astype("float32")
            # Realized variance over (t, t+h]: sum of squared daily log returns.
            feats[f"var_{h}"] = (logret ** 2).rolling(h).sum().shift(-h).astype("float32")
        # Variance-forecast inputs (daily variances over trailing windows).
        for w in (5, 21, 63, 252):
            feats[f"v{w}"] = (logret.rolling(w, min_periods=int(w * 0.8)).var()).astype("float32")
        feats = feats.iloc[F.WARMUP:]
        feats["symbol"] = sym
        parts.append(feats)
    panel = pd.concat(parts)
    panel.index.name = "date"
    panel = panel.set_index("symbol", append=True).sort_index()
    return panel


def add_labels(panel: pd.DataFrame, horizons: tuple[int, ...] = HORIZONS) -> pd.DataFrame:
    """up_h (absolute), out_h (beats the cross-sectional median), bigdd_h."""
    for h in horizons:
        fwd = panel[f"fwd_{h}"]
        panel[f"up_{h}"] = np.where(fwd.isna(), np.nan, (fwd > 0).astype("float32"))
        med = fwd.groupby(level="date").transform("median")
        panel[f"out_{h}"] = np.where(fwd.isna(), np.nan, (fwd > med).astype("float32"))
        dd = panel[f"dd_{h}"]
        panel[f"bigdd_{h}"] = np.where(dd.isna(), np.nan, (dd <= np.log(1 - DRAWDOWN_THRESHOLD)).astype("float32"))
    return panel
