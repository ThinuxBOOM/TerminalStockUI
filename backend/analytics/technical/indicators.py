"""Deterministic technical indicators (Milestone 2).

Pure functions over OHLCV series: SMA, EMA, RSI, MACD, Bollinger Bands,
ATR, realized volatility, volume anomalies. No network, no randomness;
identical inputs -> identical outputs.

Conventions:
  * Time-series outputs preserve the input index; leading warmup rows are NaN.
  * Input NaNs are dropped before computation -> quality "degraded".
  * Too-short / missing series -> "unavailable" with a reason (never raises).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ..common import (
    DEGRADED,
    MetricResult,
    align_series,
    quality_of,
    unavailable,
    validate_series,
)

SMA_FORMULA = "SMA_t(w) = mean(close[t-w+1 .. t])"
EMA_FORMULA = "EMA_t(w) = alpha*close_t + (1-alpha)*EMA_{t-1}, alpha = 2/(w+1), adjust=False"
RSI_FORMULA = (
    "RSI_t(w) = 100 - 100/(1 + RS_t); RS_t = avg_gain_t/avg_loss_t with "
    "Wilder smoothing avg_t = (avg_{t-1}*(w-1) + value_t)/w "
    "(ewm alpha=1/w, adjust=False); RSI=100 when avg_loss=0 and avg_gain>0"
)
MACD_FORMULA = (
    "MACD = EMA(fast) - EMA(slow); signal = EMA(MACD, signal_w); "
    "histogram = MACD - signal (all EMAs adjust=False)"
)
BOLLINGER_FORMULA = (
    "middle = SMA(w); upper/lower = middle +/- k*std(w, ddof=1); "
    "bandwidth = (upper-lower)/middle; %B = (close-lower)/(upper-lower)"
)
ATR_FORMULA = (
    "TR_t = max(high-low, |high-close_{t-1}|, |low-close_{t-1}|); "
    "ATR_t = Wilder mean of TR (ewm alpha=1/w, adjust=False)"
)
VOLATILITY_FORMULA = (
    "vol_t = std(log(close_t/close_{t-1}), window w, ddof=1) * sqrt(annualization)"
)
VOLUME_ANOMALY_FORMULA = (
    "z_t = (volume_t - mean(volume[t-w+1..t])) / std(volume[t-w+1..t], ddof=1); "
    "z=0 where std=0; anomaly flag = |z_last| > threshold"
)


def _check_window(window: int) -> None:
    if not isinstance(window, (int, np.integer)) or int(window) < 2:
        raise ValueError(f"window must be an integer >= 2, got {window!r}")


def sma(close: Any, window: int = 20) -> MetricResult:
    """Simple moving average of `close` over `window` periods."""
    _check_window(window)
    series, dropped, error = validate_series(close, "close", min_length=window)
    if error is not None:
        return unavailable(SMA_FORMULA, ["close"], error)
    values = series.rolling(window=window, min_periods=window).mean()
    return MetricResult(values, SMA_FORMULA, ("close",), quality_of(dropped),
                        reason=f"dropped {dropped} NaN observations" if dropped else None)


def ema(close: Any, window: int = 20) -> MetricResult:
    """Exponential moving average of `close` (span=`window`, adjust=False)."""
    _check_window(window)
    series, dropped, error = validate_series(close, "close", min_length=window)
    if error is not None:
        return unavailable(EMA_FORMULA, ["close"], error)
    values = series.ewm(span=window, adjust=False, min_periods=window).mean()
    return MetricResult(values, EMA_FORMULA, ("close",), quality_of(dropped),
                        reason=f"dropped {dropped} NaN observations" if dropped else None)


def rsi(close: Any, window: int = 14) -> MetricResult:
    """Relative Strength Index with Wilder smoothing, in [0, 100]."""
    _check_window(window)
    series, dropped, error = validate_series(close, "close", min_length=window + 1)
    if error is not None:
        return unavailable(RSI_FORMULA, ["close"], error)
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()
    rs = avg_gain / avg_loss
    values = 100.0 - (100.0 / (1.0 + rs))
    values = values.mask((avg_loss == 0) & (avg_gain > 0), 100.0)
    values = values.mask((avg_loss == 0) & (avg_gain == 0), 0.0)
    return MetricResult(values, RSI_FORMULA, ("close",), quality_of(dropped),
                        reason=f"dropped {dropped} NaN observations" if dropped else None)


def macd(close: Any, fast: int = 12, slow: int = 26, signal: int = 9) -> MetricResult:
    """MACD line, signal line and histogram as a DataFrame."""
    for name, param in (("fast", fast), ("slow", slow), ("signal", signal)):
        if not isinstance(param, (int, np.integer)) or int(param) < 2:
            raise ValueError(f"{name} must be an integer >= 2, got {param!r}")
    if not fast < slow:
        raise ValueError(f"require fast < slow, got fast={fast} slow={slow}")
    series, dropped, error = validate_series(close, "close", min_length=slow + 1)
    if error is not None:
        return unavailable(MACD_FORMULA, ["close"], error)
    ema_fast = series.ewm(span=fast, adjust=False, min_periods=1).mean()
    ema_slow = series.ewm(span=slow, adjust=False, min_periods=1).mean()
    line = ema_fast - ema_slow
    signal_line = line.ewm(span=signal, adjust=False, min_periods=1).mean()
    frame = pd.DataFrame(
        {"macd": line, "signal": signal_line, "histogram": line - signal_line}
    )
    return MetricResult(frame, MACD_FORMULA, ("close",), quality_of(dropped),
                        reason=f"dropped {dropped} NaN observations" if dropped else None)


def bollinger(close: Any, window: int = 20, num_std: float = 2.0) -> MetricResult:
    """Bollinger Bands: middle/upper/lower plus bandwidth and %B."""
    _check_window(window)
    if not float(num_std) > 0:
        raise ValueError(f"num_std must be > 0, got {num_std!r}")
    series, dropped, error = validate_series(close, "close", min_length=window)
    if error is not None:
        return unavailable(BOLLINGER_FORMULA, ["close"], error)
    middle = series.rolling(window=window, min_periods=window).mean()
    std = series.rolling(window=window, min_periods=window).std(ddof=1)
    upper = middle + float(num_std) * std
    lower = middle - float(num_std) * std
    width = upper - lower
    with np.errstate(divide="ignore", invalid="ignore"):
        bandwidth = (width / middle).where(middle != 0)
        # Standard %B: 0 at the lower band, 0.5 at the middle, 1 at upper.
        percent_b = ((series - lower) / width).where(width != 0)
    frame = pd.DataFrame(
        {"middle": middle, "upper": upper, "lower": lower,
         "bandwidth": bandwidth, "percent_b": percent_b}
    )
    return MetricResult(frame, BOLLINGER_FORMULA, ("close",), quality_of(dropped),
                        reason=f"dropped {dropped} NaN observations" if dropped else None)


def atr(high: Any, low: Any, close: Any, window: int = 14) -> MetricResult:
    """Average True Range with Wilder smoothing."""
    _check_window(window)
    frame, dropped, error = align_series(
        {"high": high, "low": low, "close": close}, min_length=window + 1
    )
    if error is not None:
        return unavailable(ATR_FORMULA, ["high", "low", "close"], error)
    prev_close = frame["close"].shift(1)
    true_range = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - prev_close).abs(),
            (frame["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    values = true_range.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()
    return MetricResult(values, ATR_FORMULA, ("high", "low", "close"),
                        quality_of(dropped),
                        reason=f"dropped {dropped} incomplete observations" if dropped else None)


def volatility(
    close: Any, window: int = 21, annualization: int = 252
) -> MetricResult:
    """Annualized realized volatility from log returns (rolling std, ddof=1)."""
    _check_window(window)
    if not int(annualization) > 0:
        raise ValueError(f"annualization must be > 0, got {annualization!r}")
    series, dropped, error = validate_series(close, "close", min_length=window + 1)
    if error is not None:
        return unavailable(VOLATILITY_FORMULA, ["close"], error)
    log_returns = np.log(series / series.shift(1))
    values = log_returns.rolling(window=window, min_periods=window).std(ddof=1) * np.sqrt(
        float(annualization)
    )
    return MetricResult(values, VOLATILITY_FORMULA, ("close",), quality_of(dropped),
                        reason=f"dropped {dropped} NaN observations" if dropped else None)


def volume_anomaly(volume: Any, window: int = 20) -> MetricResult:
    """Rolling z-score of volume vs its own trailing window."""
    _check_window(window)
    series, dropped, error = validate_series(volume, "volume", min_length=window)
    if error is not None:
        return unavailable(VOLUME_ANOMALY_FORMULA, ["volume"], error)
    roll_mean = series.rolling(window=window, min_periods=window).mean()
    roll_std = series.rolling(window=window, min_periods=window).std(ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        z_scores = (series - roll_mean) / roll_std
    z_scores = z_scores.mask((roll_std == 0) & z_scores.notna(), 0.0)
    return MetricResult(z_scores, VOLUME_ANOMALY_FORMULA, ("volume",),
                        quality_of(dropped),
                        reason=f"dropped {dropped} NaN observations" if dropped else None)


def is_volume_anomaly(
    volume: Any, window: int = 20, z_threshold: float = 2.0
) -> MetricResult:
    """Scalar flag: is the latest volume anomalous (|z| beyond threshold)?"""
    if not float(z_threshold) > 0:
        raise ValueError(f"z_threshold must be > 0, got {z_threshold!r}")
    scores = volume_anomaly(volume, window=window)
    if not scores.is_available:
        return scores
    z_scores = scores.value.dropna()
    if len(z_scores) == 0:
        return unavailable(
            VOLUME_ANOMALY_FORMULA, ["volume"],
            "volume series has no complete rolling window",
        )
    last_z = float(z_scores.iloc[-1])
    return MetricResult(
        value=bool(abs(last_z) > float(z_threshold)),
        formula=VOLUME_ANOMALY_FORMULA + f"; flag = |z_last| > {float(z_threshold)}",
        source_fields=("volume",),
        quality_flag=scores.quality_flag,
        reason=scores.reason,
    )


__all__ = [
    "sma", "ema", "rsi", "macd", "bollinger", "atr",
    "volatility", "volume_anomaly", "is_volume_anomaly",
]
