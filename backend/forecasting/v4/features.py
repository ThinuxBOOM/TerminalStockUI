"""Scale-free daily features, identical at training and serving time.

Every feature is a ratio, a return in volatility units, or a bounded
oscillator, so one pooled model can score a $20 and a $2,000 stock alike.
Row ``t`` uses data up to and including the close of day ``t`` only.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

#: Bars needed before the first complete feature row (252d windows + 1).
WARMUP = 253

STOCK_FEATURES = [
    "mom_5", "mom_21", "mom_63", "mom_126", "mom_12_1",
    "rev_1", "dist_sma50", "dist_sma200", "dd_252", "rsi_14",
    "vol_ratio_21_252", "vol_ratio_63_252", "log_vol_63", "abn_volume", "range_ratio",
]
MARKET_FEATURES = ["mkt_mom_21", "mkt_mom_63", "mkt_dist_sma200", "mkt_vol_ratio", "rel_mom_63"]
FEATURES = STOCK_FEATURES + MARKET_FEATURES


def _vol(logret: pd.Series, window: int) -> pd.Series:
    return logret.rolling(window, min_periods=int(window * 0.8)).std()


def stock_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Per-symbol features from adjusted OHLCV (index: session dates)."""
    close = frame["close"].astype(float)
    logp = np.log(close)
    r = logp.diff()
    vol21, vol63, vol252 = _vol(r, 21), _vol(r, 63), _vol(r, 252)
    base = vol63.replace(0, np.nan)

    def mom(k: int) -> pd.Series:
        return (logp - logp.shift(k)) / (base * np.sqrt(k))

    out = pd.DataFrame(index=frame.index)
    out["mom_5"] = mom(5)
    out["mom_21"] = mom(21)
    out["mom_63"] = mom(63)
    out["mom_126"] = mom(126)
    # Classic 12-1 momentum: last year's return excluding the latest month.
    out["mom_12_1"] = (logp.shift(21) - logp.shift(252)) / (base * np.sqrt(231))
    out["rev_1"] = r / base
    out["dist_sma50"] = (logp - np.log(close.rolling(50, min_periods=40).mean())) / base
    out["dist_sma200"] = (logp - np.log(close.rolling(200, min_periods=160).mean())) / base
    out["dd_252"] = logp - np.log(close.rolling(252, min_periods=200).max())
    gain = r.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-r.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    out["rsi_14"] = (gain / (gain + loss) - 0.5) * 2
    out["vol_ratio_21_252"] = np.log(vol21 / vol252)
    out["vol_ratio_63_252"] = np.log(vol63 / vol252)
    out["log_vol_63"] = np.log(vol63 * np.sqrt(252))
    if "volume" in frame:
        dollar = (frame["volume"].astype(float) * close).replace(0, np.nan)
        out["abn_volume"] = np.log(dollar.rolling(5, min_periods=3).mean() / dollar.rolling(63, min_periods=40).mean())
    else:
        out["abn_volume"] = np.nan
    if {"high", "low"} <= set(frame.columns):
        hl = np.log(frame["high"].astype(float) / frame["low"].astype(float)).replace(0, np.nan)
        out["range_ratio"] = np.log(hl.rolling(5, min_periods=3).mean() / hl.rolling(63, min_periods=40).mean())
    else:
        out["range_ratio"] = np.nan
    return out.replace([np.inf, -np.inf], np.nan)


def market_features(market: pd.DataFrame) -> pd.DataFrame:
    close = market["close"].astype(float)
    logp = np.log(close)
    r = logp.diff()
    vol63 = _vol(r, 63)
    out = pd.DataFrame(index=market.index)
    out["mkt_mom_21"] = (logp - logp.shift(21)) / (vol63 * np.sqrt(21))
    out["mkt_mom_63"] = (logp - logp.shift(63)) / (vol63 * np.sqrt(63))
    out["mkt_dist_sma200"] = (logp - np.log(close.rolling(200, min_periods=160).mean())) / vol63
    out["mkt_vol_ratio"] = np.log(_vol(r, 21) / _vol(r, 252))
    out["_mkt_logret_63"] = logp - logp.shift(63)
    return out.replace([np.inf, -np.inf], np.nan)


def build(frame: pd.DataFrame, market: pd.DataFrame | None) -> pd.DataFrame:
    """All FEATURES for one symbol; market columns NaN when no market series."""
    feats = stock_features(frame)
    if market is not None and len(market):
        mk = market_features(market).reindex(feats.index, method="ffill")
        stock_ret_63 = np.log(frame["close"].astype(float)).diff(63)
        vol63 = np.log(frame["close"].astype(float)).diff().rolling(63, min_periods=50).std()
        feats = feats.join(mk.drop(columns="_mkt_logret_63"))
        feats["rel_mom_63"] = (stock_ret_63 - mk["_mkt_logret_63"]) / (vol63 * np.sqrt(63))
    else:
        for col in MARKET_FEATURES:
            feats[col] = np.nan
    return feats[FEATURES].replace([np.inf, -np.inf], np.nan)


def forward_log_return(close: pd.Series, horizon: int) -> pd.Series:
    """log(close[t+h] / close[t]); NaN where t+h is not yet observed."""
    logp = np.log(close.astype(float))
    return logp.shift(-horizon) - logp


def forward_max_drawdown(close: pd.Series, horizon: int) -> pd.Series:
    """Worst close-to-close drop from close[t] within the next h sessions (<= 0)."""
    from numpy.lib.stride_tricks import sliding_window_view

    c = close.astype(float).to_numpy()
    n = len(c)
    out = np.full(n, np.nan)
    if n > horizon:
        lows = sliding_window_view(c[1:], horizon).min(axis=1)  # lows[i] = min(c[i+1:i+1+h])
        out[: n - horizon] = np.minimum(0.0, np.log(lows / c[: n - horizon]))
    return pd.Series(out, index=close.index)
