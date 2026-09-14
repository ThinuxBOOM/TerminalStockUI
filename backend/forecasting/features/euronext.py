"""Euronext-aware features for OneMarket Analyzer forecasting (Milestone 7).

Deterministic, past-only, no AI, no network. All features at row ``t`` use
only OHLCV rows with index ``<= t`` (trailing windows / shifts), so appending
future rows never changes already-computed rows.

Market assumptions (documented, no live data fabricated):
  * Continuous session 09:00-17:30 local (Europe/Paris, Europe/Amsterdam,
    Europe/Brussels) with NO lunch break. Unlike SSE (09:30-11:30 /
    13:00-15:00 with a 90-min midday gap), no lunch-gap volatility
    adjustment is needed or applied here. Daily OHLCV bars aggregate the
    full continuous session.
  * No daily price limits. Unlike SSE generic A-share +/-10% limits (which
    censor tails and motivate limit-distance features), Euronext has no
    hard daily limit band, so NO limit-distance / limit-proximity feature
    is computed here. Tail robustness in the drift model is a statistical
    winsor cap only, not a limit-implied band.
  * Euronext tick-size regimes (MiFID II / Euronext tick tables: tick
    depends on price level and liquidity band) are NOTED but NOT modeled:
    daily bars cannot observe tick-constrained micro-price dynamics, and
    all features below are scale-free ratios. Intraday extensions needing
    spread/tick costs must model the tick table explicitly.
  * Currency: all features are ratios (returns / volatilities / volume
    ratios / gaps), hence invariant to EUR price scale (multiplying every
    price by a constant leaves every feature unchanged). Volume is never
    mixed with prices except as a pure volume ratio.
  * Settlement note (not parameterized): Euronext settles T+2 (moving to
    T+1 under CSDR in Oct 2027); no settlement-driven turnover smoothing
    beyond the 5-day volume-ratio proxy is applied here.

Formulas (NaN-safe; division by zero yields NaN, never inf):
  * ``mom_5_t = close_t / close_{t-5} - 1`` (likewise mom_10, mom_20).
  * ``vol_21_t = std(logret_{t-20..t}) * sqrt(252)`` (annualized trailing
    volatility, ddof=1).
  * ``rsi_14_t`` = Wilder RSI(14) on closes (ewm alpha=1/14, flat -> 50).
  * ``turnover_5d_t = volume_t / mean(volume_{t-5..t-1})`` (ex-current
    trailing mean; volume-ratio proxy only, not settlement records).
  * ``gap_proxy_t = (open_t - close_{t-1}) / close_{t-1}``
    (overnight/close-to-close gap proxy from daily bars; first row NaN).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .features import log_returns, validate_ohlcv

EUX_FEATURE_VERSION = "eux-features-v1"
EUX_FEATURE_COLUMNS = (
    "mom_5",
    "mom_10",
    "mom_20",
    "vol_21",
    "rsi_14",
    "turnover_5d",
    "gap_proxy",
)

TURNOVER_WINDOW = 5
VOL_WINDOW = 21
RSI_WINDOW = 14
ANNUALIZATION = 252


def build_euronext_features(
    ohlcv: pd.DataFrame,
    turnover_window: int = TURNOVER_WINDOW,
    vol_window: int = VOL_WINDOW,
    rsi_window: int = RSI_WINDOW,
    annualization: int = ANNUALIZATION,
) -> pd.DataFrame:
    """Build Euronext past-only features, dropping warmup NaN rows.

    Args:
        ohlcv: OHLCV frame with open/high/low/close/volume, sorted
            oldest-first (validated via :func:`validate_ohlcv`).
        turnover_window: Lookback for the turnover proxy (default 5).
        vol_window: Lookback for annualized trailing volatility (default 21).
        rsi_window: Wilder RSI window (default 14).
        annualization: Trading days per year for vol scaling (default 252).

    Returns:
        DataFrame with :data:`EUX_FEATURE_COLUMNS`, finite values only.
    """
    if int(turnover_window) < 2:
        raise ValueError(f"turnover_window must be >= 2, got {turnover_window!r}")
    if int(vol_window) < 2:
        raise ValueError(f"vol_window must be >= 2, got {vol_window!r}")
    if int(rsi_window) < 2:
        raise ValueError(f"rsi_window must be >= 2, got {rsi_window!r}")
    if int(annualization) < 1:
        raise ValueError(f"annualization must be >= 1, got {annualization!r}")
    frame = validate_ohlcv(ohlcv)
    close = frame["close"].astype(float)
    volume = frame["volume"].astype(float)
    open_ = frame["open"].astype(float)
    prev_close = close.shift(1)

    with np.errstate(divide="ignore", invalid="ignore"):
        mom_5 = close / close.shift(5) - 1.0
        mom_10 = close / close.shift(10) - 1.0
        mom_20 = close / close.shift(20) - 1.0

    lret = log_returns(close)
    with np.errstate(divide="ignore", invalid="ignore"):
        vol_21 = (
            lret.rolling(int(vol_window), min_periods=int(vol_window)).std(ddof=1)
            * np.sqrt(float(annualization))
        )

    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(
        alpha=1.0 / float(rsi_window), adjust=False,
        min_periods=int(rsi_window),
    ).mean()
    avg_loss = loss.ewm(
        alpha=1.0 / float(rsi_window), adjust=False,
        min_periods=int(rsi_window),
    ).mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        rsi = 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))
    rsi = rsi.mask((avg_loss == 0) & (avg_gain > 0), 100.0)
    # Flat history is neutral 50, consistent with core features + analytics RSI.
    rsi = rsi.mask((avg_loss == 0) & (avg_gain == 0), 50.0)

    # Ex-current trailing mean: spike bar must not dampen its own denominator.
    vol_mean = volume.shift(1).rolling(int(turnover_window), min_periods=int(turnover_window)).mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        turnover = volume / vol_mean
    turnover = turnover.mask((vol_mean == 0) | (~np.isfinite(turnover)))

    with np.errstate(divide="ignore", invalid="ignore"):
        gap_proxy = (open_ - prev_close) / prev_close
    gap_proxy = gap_proxy.mask(~np.isfinite(gap_proxy))

    features = pd.DataFrame(index=frame.index)
    features["mom_5"] = mom_5
    features["mom_10"] = mom_10
    features["mom_20"] = mom_20
    features["vol_21"] = vol_21
    features["rsi_14"] = rsi
    features["turnover_5d"] = turnover
    features["gap_proxy"] = gap_proxy
    return features.dropna()


__all__ = [
    "EUX_FEATURE_VERSION",
    "EUX_FEATURE_COLUMNS",
    "TURNOVER_WINDOW",
    "VOL_WINDOW",
    "RSI_WINDOW",
    "ANNUALIZATION",
    "build_euronext_features",
]
