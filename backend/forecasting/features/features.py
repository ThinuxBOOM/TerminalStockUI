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


def validate_ohlcv(frame: Any) -> pd.DataFrame:
    """Validate an OHLCV frame; raise ValueError listing the problem.

    Requires open/high/low/close/volume columns, finite numerics, a
    unique monotonic index (callers must pass corporate-action-adjusted
    closes, sorted oldest-first).
    """
    if not isinstance(frame, pd.DataFrame):
        raise ValueError("ohlcv input must be a pandas DataFrame")
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

    vol_mean = frame["volume"].rolling(volume_window, min_periods=volume_window).mean()
    vol_std = frame["volume"].rolling(volume_window, min_periods=volume_window).std(ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        volume_z = (frame["volume"] - vol_mean) / vol_std
    volume_z = volume_z.mask((vol_std == 0) & volume_z.notna(), 0.0)

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


def future_return(close: pd.Series, horizon_days: int) -> pd.Series:
    """Forward simple return close_{t+h}/close_t - 1 (training labels only)."""
    if int(horizon_days) < 1:
        raise ValueError("horizon_days must be >= 1")
    close = pd.Series(close, dtype=float)
    return close.shift(-int(horizon_days)) / close - 1.0


def direction_label(close: pd.Series, horizon_days: int) -> pd.Series:
    """Binary up/down label 1.0/0.0; NaN where the horizon is unobservable."""
    forward = future_return(close, horizon_days)
    return pd.Series(
        np.where(forward.isna(), np.nan, (forward > 0).astype(float)),
        index=forward.index,
    )


def future_drawdown(close: pd.Series, horizon_days: int) -> pd.Series:
    """Forward worst peak-to-trough drawdown over the next h bars (<= 0).

    Training/evaluation helper only. O(n*h); fine for baseline scale.
    """
    if int(horizon_days) < 1:
        raise ValueError("horizon_days must be >= 1")
    prices = pd.Series(close, dtype=float).to_numpy()
    horizon = int(horizon_days)
    out = np.full(len(prices), np.nan)
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
    "REQUIRED_COLUMNS",
    "validate_ohlcv",
    "log_returns",
    "build_features",
    "future_return",
    "direction_label",
    "future_drawdown",
]
