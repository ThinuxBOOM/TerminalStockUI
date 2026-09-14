"""SSE-aware features for OneMarket Analyzer forecasting (Milestone 6).

Deterministic, past-only, no AI, no network. All features at row ``t`` use
only OHLCV rows with index ``<= t`` (trailing windows / shifts), so appending
future rows never changes already-computed rows.

Market assumptions (documented, no live data fabricated):
  * Price limits: generic A-share +/-10% daily limit computed from
    ``prev_close`` (``limit_up = prev_close * 1.10``,
    ``limit_down = prev_close * 0.90``). ST/*ST +/-5% boards are NOT
    separately modeled; callers needing them must pass an adjusted limit.
  * T+1 settlement: A-shares bought on day T cannot be sold until T+1, which
    dampens same-day reversal and concentrates rotation over several days.
    The turnover proxy ``volume / 5d-average`` smooths activity for this
    reason; it is a volume-ratio proxy only, not settlement records.
  * Lunch break: SSE continuous auction runs 09:30-11:30 and 13:00-15:00
    (90-min midday break, 225 active minutes). Daily OHLCV bars cannot
    observe the intraday lunch gap, so NO lunch-break volatility adjustment
    is applied here. Future intraday realized-vol estimators should exclude
    the lunch gap / scale by active minutes instead of 24h clock time.
  * Currency: all features are ratios (returns / distances / volume ratios),
    hence invariant to CNY price scale (multiplying every price by a constant
    leaves every feature unchanged).

Formulas (NaN-safe; division by zero yields NaN, never inf):
  * ``prev_close_t = close_{t-1}``
  * ``limit_up_dist_t = (prev_close_t * 1.10 - close_t) / prev_close_t``
  * ``limit_down_dist_t = (close_t - prev_close_t * 0.90) / prev_close_t``
  * ``limit_proximity_t = 1.0`` if
    ``min(limit_up_dist_t, limit_down_dist_t) <= 0.02`` else ``0.0``
    (within 2% of either daily limit, relative to prev_close).
  * ``turnover_5d_t = volume_t / mean(volume_{t-5..t-1})`` (ex-current
    trailing mean so spikes are undampened; inclusive would read 5x as 2.78x).
  * ``mom_5_t = close_t / close_{t-5} - 1`` (likewise mom_10, mom_20).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .features import validate_ohlcv

SSE_FEATURE_VERSION = "sse-features-v1"
SSE_FEATURE_COLUMNS = (
    "limit_up_dist",
    "limit_down_dist",
    "limit_proximity",
    "turnover_5d",
    "mom_5",
    "mom_10",
    "mom_20",
)

PRICE_LIMIT_PCT = 0.10
PROXIMITY_THRESH = 0.02
TURNOVER_WINDOW = 5


def build_sse_features(
    ohlcv: pd.DataFrame,
    limit_pct: float = PRICE_LIMIT_PCT,
    proximity_thresh: float = PROXIMITY_THRESH,
    turnover_window: int = TURNOVER_WINDOW,
) -> pd.DataFrame:
    """Build SSE-aware past-only features, dropping warmup NaN rows.

    Args:
        ohlcv: OHLCV frame with open/high/low/close/volume, sorted
            oldest-first (validated via :func:`validate_ohlcv`).
        limit_pct: Daily price-limit fraction (default 0.10 for +/-10%).
        proximity_thresh: Distance (fraction of prev_close) at or below
            which ``limit_proximity`` fires (default 0.02).
        turnover_window: Lookback for the turnover proxy (default 5).

    Returns:
        DataFrame with :data:`SSE_FEATURE_COLUMNS`, finite values only.
    """
    if not 0.0 < float(limit_pct) < 1.0:
        raise ValueError(f"limit_pct must be in (0, 1), got {limit_pct!r}")
    if not float(proximity_thresh) >= 0.0:
        raise ValueError(f"proximity_thresh must be >= 0, got {proximity_thresh!r}")
    if int(turnover_window) < 2:
        raise ValueError(f"turnover_window must be >= 2, got {turnover_window!r}")
    frame = validate_ohlcv(ohlcv)
    close = frame["close"].astype(float)
    volume = frame["volume"].astype(float)
    prev_close = close.shift(1)

    with np.errstate(divide="ignore", invalid="ignore"):
        limit_up_dist = (prev_close * (1.0 + float(limit_pct)) - close) / prev_close
        limit_down_dist = (close - prev_close * (1.0 - float(limit_pct))) / prev_close
    # Guard: non-positive prev_close cannot happen post-validation, but mask
    # defensively so no inf leaks through.
    limit_up_dist = limit_up_dist.mask(~np.isfinite(limit_up_dist))
    limit_down_dist = limit_down_dist.mask(~np.isfinite(limit_down_dist))
    # First row (no prev_close) stays NaN and is dropped as warmup.

    proximity = pd.Series(
        np.where(
            pd.concat([limit_up_dist, limit_down_dist], axis=1).min(axis=1)
            <= float(proximity_thresh),
            1.0,
            0.0,
        ),
        index=frame.index,
    )
    # Where distances are NaN (warmup), proximity is undefined -> NaN so the
    # row is dropped rather than silently labelled 0.
    proximity = proximity.mask(limit_up_dist.isna() | limit_down_dist.isna())

    # Ex-current trailing mean: spike bar must not dampen its own denominator.
    vol_mean = volume.shift(1).rolling(int(turnover_window), min_periods=int(turnover_window)).mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        turnover = volume / vol_mean
    turnover = turnover.mask((vol_mean == 0) | (~np.isfinite(turnover)))

    with np.errstate(divide="ignore", invalid="ignore"):
        mom_5 = close / close.shift(5) - 1.0
        mom_10 = close / close.shift(10) - 1.0
        mom_20 = close / close.shift(20) - 1.0

    features = pd.DataFrame(index=frame.index)
    features["limit_up_dist"] = limit_up_dist
    features["limit_down_dist"] = limit_down_dist
    features["limit_proximity"] = proximity
    features["turnover_5d"] = turnover
    features["mom_5"] = mom_5
    features["mom_10"] = mom_10
    features["mom_20"] = mom_20
    return features.dropna()


def limit_proximity_triggered(sse_features: pd.DataFrame) -> bool:
    """Return True when the latest SSE feature row is at the price limit.

    ``limit_proximity == 1.0`` on the last row means the close sits within
    ``PROXIMITY_THRESH`` (2%) of either daily limit. Empty frames return
    False (no signal, never an error).
    """
    try:
        frame = pd.DataFrame(sse_features)
    except (ValueError, TypeError):
        return False
    if len(frame) == 0 or "limit_proximity" not in frame.columns:
        return False
    try:
        return bool(float(frame["limit_proximity"].iloc[-1]) == 1.0)
    except (ValueError, TypeError, IndexError):
        return False


__all__ = [
    "SSE_FEATURE_VERSION",
    "SSE_FEATURE_COLUMNS",
    "PRICE_LIMIT_PCT",
    "PROXIMITY_THRESH",
    "TURNOVER_WINDOW",
    "build_sse_features",
    "limit_proximity_triggered",
]
