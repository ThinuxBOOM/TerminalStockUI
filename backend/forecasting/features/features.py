"""Leakage-safe feature engineering for the forecasting baselines.

No-leakage contract: every feature at row t is computed ONLY from OHLCV
rows with index <= t (trailing windows, past returns). Appending future
rows never changes already-computed rows (covered by leakage tests).
Warmup caveat: rolling/ewm features depend on where the input series
starts, so walk-forward folds must always fit on a contiguous history
prefix — never on a mid-series slice without warmup (see backtesting).

Label helpers (future_return / direction_label / future_drawdown) peek
ahead by construction and are for supervised training/evaluation ONLY,
always behind the walk-forward splitter + no-leakage guard.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

FEATURE_VERSION = "features-v1"
REQUIRED_COLUMNS = ("open", "high", "low", "close", "volume")
FEATURE_COLUMNS = ("ret_1", "ret_5", "mom_21", "vol_21", "rsi_14",
                   "volume_z20", "range_pct")
#: Extended past-only features (v2, additive over v1). Consumed by the
#: logistic model via build_extended_features(); build_features() output
#: is unchanged for backward compat (tests + persisted versions).
EXTENDED_FEATURE_VERSION = "features-v2"
EXTENDED_FEATURE_COLUMNS = (
    "mom_63", "vol_63", "trail_dd_63", "range_ma_21", "volume_z63",
    "ret_skew_21", "rsi_lag5",
)


def validate_ohlcv(frame: Any) -> pd.DataFrame:
    """Validate an OHLCV frame; raise ValueError listing the problem.

    Requires open/high/low/close/volume columns, finite numerics, a
    unique monotonic index (callers must pass corporate-action-adjusted
    closes, sorted oldest-first).
    """
    if not isinstance(frame, pd.DataFrame):
        raise ValueError("ohlcv input must be a pandas DataFrame")
    if len(frame) == 0:
        raise ValueError("ohlcv frame is empty")
    missing = [c for c in REQUIRED_COLUMNS if c not in frame.columns]
    if missing:
        raise ValueError(f"ohlcv missing columns: {missing}")
    clean = frame.loc[:, list(REQUIRED_COLUMNS)].copy()
    for column in REQUIRED_COLUMNS:
        clean[column] = pd.to_numeric(clean[column], errors="coerce")
    bad = clean.isna().sum()
    bad = bad[bad > 0]
    if len(bad):
        raise ValueError(f"ohlcv has NaN/non-numeric values: {bad.to_dict()}")
    if not np.isfinite(clean.to_numpy()).all():
        raise ValueError("ohlcv contains infinite values")
    if clean.index.has_duplicates:
        raise ValueError("ohlcv index has duplicate labels")
    if not clean.index.is_monotonic_increasing:
        raise ValueError("ohlcv index must be sorted oldest-first")
    if (clean["close"] <= 0).any() or (clean["volume"] < 0).any():
        raise ValueError("ohlcv requires close > 0 and volume >= 0")
    return clean


def log_returns(close: pd.Series) -> pd.Series:
    """Log returns ln(close_t / close_{t-1}); first row NaN."""
    close = pd.Series(close, dtype=float)
    return np.log(close / close.shift(1))


def build_features(
    ohlcv: pd.DataFrame,
    vol_window: int = 21,
    mom_window: int = 21,
    rsi_window: int = 14,
    volume_window: int = 20,
    annualization: int = 252,
) -> pd.DataFrame:
    """Build the v1 baseline feature frame (past data only), NaN rows dropped.

    Columns: ret_1 (1d log return), ret_5 (trailing 5d log return),
    mom_21 (21d simple trailing return), vol_21 (annualized trailing
    volatility), rsi_14 (Wilder RSI), volume_z20 (volume z-score),
    range_pct ((high-low)/close).
    """
    for name, value in (("vol_window", vol_window), ("mom_window", mom_window),
                        ("rsi_window", rsi_window), ("volume_window", volume_window)):
        if int(value) < 2:
            raise ValueError(f"{name} must be >= 2, got {value!r}")
    frame = validate_ohlcv(ohlcv)
    close = frame["close"]
    lret = log_returns(close)

    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / rsi_window, adjust=False,
                        min_periods=rsi_window).mean()
    avg_loss = loss.ewm(alpha=1.0 / rsi_window, adjust=False,
                        min_periods=rsi_window).mean()
    rsi = 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))
    rsi = rsi.mask((avg_loss == 0) & (avg_gain > 0), 100.0)
    # Flat history is neutral 50, consistent with analytics/technical RSI.
    rsi = rsi.mask((avg_loss == 0) & (avg_gain == 0), 50.0)

    vol_mean = frame["volume"].rolling(volume_window, min_periods=volume_window).mean()
    vol_std = frame["volume"].rolling(volume_window, min_periods=volume_window).std(ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        volume_z = (frame["volume"] - vol_mean) / vol_std
    # std==0 -> 0/0=NaN; contract is z=0 there (mask on std alone).
    volume_z = volume_z.mask(vol_std == 0, 0.0)

    features = pd.DataFrame(index=frame.index)
    features["ret_1"] = lret
    features["ret_5"] = lret.rolling(5, min_periods=5).sum()
    features["mom_21"] = close / close.shift(mom_window) - 1.0
    features["vol_21"] = (
        lret.rolling(vol_window, min_periods=vol_window).std(ddof=1)
        * np.sqrt(float(annualization))
    )
    features["rsi_14"] = rsi
    features["volume_z20"] = volume_z
    features["range_pct"] = (frame["high"] - frame["low"]) / close
    return features.dropna()


def build_extended_features(ohlcv: pd.DataFrame) -> pd.DataFrame:
    """Additive past-only features over :func:`build_features` (v2).

    All columns use trailing data only (index <= t), so the no-leakage
    contract and append-invariance of v1 are preserved. Returns v1 columns
    plus ``EXTENDED_FEATURE_COLUMNS``; rows with any NaN are dropped.

    Extra signals from past market data:
      * mom_63: 63d trailing return (quarterly trend persistence)
      * vol_63: 63d annualized trailing volatility (risk regime)
      * trail_dd_63: trailing 63d peak-to-trough drawdown depth (<= 0)
      * range_ma_21: 21d mean of daily range_pct (liquidity/vol proxy)
      * volume_z63: 63d volume z-score (participation shock)
      * ret_skew_21: 21d skew of log returns (asymmetry)
      * rsi_lag5: RSI 5 bars ago (momentum persistence, still past-only)
    """
    base = build_features(ohlcv)
    frame = validate_ohlcv(ohlcv)
    close = frame["close"]
    lret = log_returns(close)

    mom_63 = close / close.shift(63) - 1.0
    vol_63 = lret.rolling(63, min_periods=63).std(ddof=1) * np.sqrt(252.0)
    # Trailing drawdown depth: (close / trailing-63d running peak) - 1.
    peak_63 = close.rolling(63, min_periods=63).max()
    with np.errstate(divide="ignore", invalid="ignore"):
        trail_dd = close / peak_63 - 1.0
    trail_dd = trail_dd.clip(upper=0.0)
    range_pct = (frame["high"] - frame["low"]) / close
    range_ma = range_pct.rolling(21, min_periods=21).mean()
    vol_mean63 = frame["volume"].rolling(63, min_periods=63).mean()
    vol_std63 = frame["volume"].rolling(63, min_periods=63).std(ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        volume_z63 = (frame["volume"] - vol_mean63) / vol_std63
    volume_z63 = volume_z63.mask((vol_std63 == 0) & volume_z63.notna(), 0.0)
    ret_skew = lret.rolling(21, min_periods=21).skew()

    # RSI recomputed identically to build_features (incl. flat-history 50),
    # then lagged 5 (past-only).
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / 14, adjust=False, min_periods=14).mean()
    avg_loss = loss.ewm(alpha=1.0 / 14, adjust=False, min_periods=14).mean()
    rsi = 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))
    rsi = rsi.mask((avg_loss == 0) & (avg_gain > 0), 100.0)
    rsi = rsi.mask((avg_loss == 0) & (avg_gain == 0), 50.0)
    rsi_lag5 = rsi.shift(5)

    extra = pd.DataFrame(index=frame.index)
    extra["mom_63"] = mom_63
    extra["vol_63"] = vol_63
    extra["trail_dd_63"] = trail_dd
    extra["range_ma_21"] = range_ma
    extra["volume_z63"] = volume_z63
    extra["ret_skew_21"] = ret_skew
    extra["rsi_lag5"] = rsi_lag5
    joined = base.join(extra, how="inner")
    return joined.dropna()


def future_return(close: pd.Series, horizon_days: int) -> pd.Series:
    """Forward simple return close_{t+h}/close_t - 1 (training labels only)."""
    try:
        horizon = int(horizon_days)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError("horizon_days must be >= 1") from exc
    if horizon < 1:
        raise ValueError("horizon_days must be >= 1")
    close = pd.Series(close, dtype=float)
    return close.shift(-horizon) / close - 1.0


def direction_label(close: pd.Series, horizon_days: int) -> pd.Series:
    """Binary up/down label 1.0/0.0; NaN where the horizon is unobservable."""
    forward = future_return(close, horizon_days)
    return pd.Series(
        np.where(forward.isna(), np.nan, (forward > 0).astype(float)),
        index=forward.index,
    )


def future_drawdown(close: pd.Series, horizon_days: int) -> pd.Series:
    """Forward worst peak-to-trough drawdown over the next h bars (<= 0).

    Training/evaluation helper only. Vectorized O(n*h) in C via a strided
    window view (single pass, no Python inner loop); falls back to the
    scalar loop only when ``sliding_window_view`` is unavailable.
    """
    try:
        horizon = int(horizon_days)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError("horizon_days must be >= 1") from exc
    if horizon < 1:
        raise ValueError("horizon_days must be >= 1")
    idx = pd.Series(close).index
    # Short-circuit thin/empty history: same all-NaN shape the loop would
    # produce, without O(n*h) work.
    try:
        n = len(idx)
    except TypeError:
        n = 0
    if n < 2:
        return pd.Series(np.full(n, np.nan), index=idx)
    prices = pd.Series(close, dtype=float).to_numpy()
    out = np.full(len(prices), np.nan)
    # Fast path: strided windows + cummax in C (n<=250, h<=63 -> ~16k cells).
    try:
        from numpy.lib.stride_tricks import sliding_window_view as _swv

        n_len = len(prices)
        padded = np.concatenate([prices, np.full(horizon, np.nan)])
        # windows[i] = prices[i : i+h+1]
        windows = _swv(padded, horizon + 1)[:n_len]
        finite_mask = np.isfinite(windows)
        # First price must be finite and positive; else row stays NaN.
        first = windows[:, 0]
        valid_row = np.isfinite(first) & (first > 0) & (finite_mask.sum(axis=1) >= 2)
        if valid_row.any():
            safe = np.where(finite_mask, windows, -np.inf)
            peaks = np.maximum.accumulate(safe, axis=1)
            with np.errstate(divide="ignore", invalid="ignore"):
                dd = np.where(
                    finite_mask & (peaks > 0),
                    windows / np.where(peaks == 0, 1.0, peaks) - 1.0,
                    np.nan,
                )
            # Drawdown is <= 0 by construction; ignore NaN windows.
            with np.errstate(invalid="ignore"):
                worst = np.nanmin(np.where(dd > 0, 0.0, dd), axis=1)
            # Rows with < 2 finite observations stay NaN (match legacy).
            out[valid_row] = np.where(
                np.isfinite(worst[valid_row]), np.minimum(worst[valid_row], 0.0), np.nan
            )
            out[~valid_row] = np.nan
        return pd.Series(out, index=pd.Series(close).index)
    except Exception:
        pass
    for i in range(len(prices)):
        window = prices[i:i + horizon + 1]
        if len(window) < 2 or not np.isfinite(window).all() or window[0] <= 0:
            continue
        peak = window[0]
        worst = 0.0
        for price in window[1:]:
            peak = max(peak, price)
            worst = min(worst, price / peak - 1.0)
        out[i] = worst
    return pd.Series(out, index=pd.Series(close).index)


__all__ = [
    "FEATURE_VERSION",
    "FEATURE_COLUMNS",
    "EXTENDED_FEATURE_VERSION",
    "EXTENDED_FEATURE_COLUMNS",
    "REQUIRED_COLUMNS",
    "validate_ohlcv",
    "log_returns",
    "build_features",
    "build_extended_features",
    "future_return",
    "direction_label",
    "future_drawdown",
]
