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

import hashlib
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
#: Corporate-action lineage gates (single-day SIMPLE returns): bars are
#: assumed split/dividend-adjusted upstream (ingest auto_adjust). An
#: unadjusted 2:1 split prints as a -50% day; an unadjusted 1:2 reverse
#: split prints as a +100% day. These gates only flag lineage for the
#: service-level confidence cap; they never alter prices here.
CORPORATE_ACTION_CRASH_DROP = -0.45
CORPORATE_ACTION_JUMP_GAIN = 0.90
#: Thin-liquidity gate: zero-volume-bar fraction above this reads thin
#: (estimates from non-trading stretches deserve less confidence).
THIN_ZERO_VOLUME_FRAC = 0.10
#: Bound on the feature-frame memo (pure-function cache; oldest dropped).
_FEATURES_CACHE_MAX = 128
_FEATURES_CACHE: dict[tuple, pd.DataFrame] = {}


def clear_feature_cache() -> None:  # test hook
    """Empty the feature-frame memo (tests + service cache reset)."""
    try:
        _FEATURES_CACHE.clear()
    except Exception:
        pass


def _ohlcv_fingerprint(clean: pd.DataFrame) -> str:
    """SHA-256 over validated required-column values + index (deterministic)."""
    h = hashlib.sha256()
    for column in REQUIRED_COLUMNS:
        h.update(bytes(np.ascontiguousarray(
            clean[column].to_numpy(dtype=np.float64))))
    try:
        idx = clean.index
        if isinstance(idx, pd.DatetimeIndex):
            h.update(bytes(np.ascontiguousarray(idx.asi8)))
        else:
            h.update(bytes(np.ascontiguousarray(idx.to_numpy())))
    except (TypeError, ValueError):
        h.update(repr(list(clean.index)).encode())
    h.update(str(len(clean)).encode())
    return h.hexdigest()


def _features_cache_get(key: tuple) -> pd.DataFrame | None:
    try:
        hit = _FEATURES_CACHE.get(key)
    except Exception:
        return None
    if isinstance(hit, pd.DataFrame):
        try:
            return hit.copy(deep=True)
        except Exception:
            return None
    return None


def _features_cache_set(key: tuple, frame: pd.DataFrame) -> None:
    try:
        if len(_FEATURES_CACHE) >= _FEATURES_CACHE_MAX:
            _FEATURES_CACHE.pop(next(iter(_FEATURES_CACHE)), None)
        _FEATURES_CACHE[key] = frame.copy(deep=True)
    except Exception:
        pass


def _check_feature_windows(
    vol_window: int, mom_window: int, rsi_window: int, volume_window: int
) -> None:
    for name, value in (("vol_window", vol_window), ("mom_window", mom_window),
                        ("rsi_window", rsi_window), ("volume_window", volume_window)):
        if int(value) < 2:
            raise ValueError(f"{name} must be >= 2, got {value!r}")


def _wilder_rsi(close: pd.Series, window: int) -> pd.Series:
    """Wilder RSI (ewm alpha=1/window); flat history reads 50 (neutral).

    Single shared implementation for the v1/v2 builders so both frames
    reuse one ewm pass with bit-identical values.
    """
    window = int(window)
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / window, adjust=False,
                        min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1.0 / window, adjust=False,
                        min_periods=window).mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        rsi = 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))
    rsi = rsi.mask((avg_loss == 0) & (avg_gain > 0), 100.0)
    # Flat history is neutral 50, consistent with analytics/technical RSI.
    rsi = rsi.mask((avg_loss == 0) & (avg_gain == 0), 50.0)
    return rsi


def _volume_zscore(volume: pd.Series, window: int) -> pd.Series:
    """Trailing volume z-score; std==0 reads 0 (no dispersion, no shock)."""
    window = int(window)
    mean = volume.rolling(window, min_periods=window).mean()
    std = volume.rolling(window, min_periods=window).std(ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        z = (volume - mean) / std
    # std==0 -> 0/0=NaN; contract is z=0 there (mask on std alone).
    return z.mask(std == 0, 0.0)


def _base_from_clean(
    clean: pd.DataFrame,
    vol_window: int = 21,
    mom_window: int = 21,
    rsi_window: int = 14,
    volume_window: int = 20,
    annualization: int = 252,
) -> tuple[pd.DataFrame, dict[str, pd.Series]]:
    """v1 columns over a validated frame + shared intermediates (lret, rsi)."""
    close = clean["close"]
    lret = log_returns(close)
    rsi = _wilder_rsi(close, int(rsi_window))
    volume_z = _volume_zscore(clean["volume"], int(volume_window))

    features = pd.DataFrame(index=clean.index)
    features["ret_1"] = lret
    features["ret_5"] = lret.rolling(5, min_periods=5).sum()
    features["mom_21"] = close / close.shift(int(mom_window)) - 1.0
    features["vol_21"] = (
        lret.rolling(int(vol_window), min_periods=int(vol_window)).std(ddof=1)
        * np.sqrt(float(annualization))
    )
    features["rsi_14"] = rsi
    features["volume_z20"] = volume_z
    features["range_pct"] = (clean["high"] - clean["low"]) / close
    return features.dropna(), {"lret": lret, "rsi": rsi}


def _extended_from_clean(
    clean: pd.DataFrame, lret: pd.Series, rsi: pd.Series
) -> pd.DataFrame:
    """v2 extra columns over a validated frame (reuses v1 lret/RSI)."""
    close = clean["close"]
    mom_63 = close / close.shift(63) - 1.0
    vol_63 = lret.rolling(63, min_periods=63).std(ddof=1) * np.sqrt(252.0)
    # Trailing drawdown depth: (close / trailing-63d running peak) - 1.
    peak_63 = close.rolling(63, min_periods=63).max()
    with np.errstate(divide="ignore", invalid="ignore"):
        trail_dd = close / peak_63 - 1.0
    trail_dd = trail_dd.clip(upper=0.0)
    range_pct = (clean["high"] - clean["low"]) / close
    range_ma = range_pct.rolling(21, min_periods=21).mean()
    vol_mean63 = clean["volume"].rolling(63, min_periods=63).mean()
    vol_std63 = clean["volume"].rolling(63, min_periods=63).std(ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        volume_z63 = (clean["volume"] - vol_mean63) / vol_std63
    volume_z63 = volume_z63.mask((vol_std63 == 0) & volume_z63.notna(), 0.0)
    ret_skew = lret.rolling(21, min_periods=21).skew()
    # RSI lagged 5 (past-only momentum persistence).
    rsi_lag5 = rsi.shift(5)

    extra = pd.DataFrame(index=clean.index)
    extra["mom_63"] = mom_63
    extra["vol_63"] = vol_63
    extra["trail_dd_63"] = trail_dd
    extra["range_ma_21"] = range_ma
    extra["volume_z63"] = volume_z63
    extra["ret_skew_21"] = ret_skew
    extra["rsi_lag5"] = rsi_lag5
    return extra


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
    range_pct ((high-low)/close). Memoized on identical inputs.
    """
    _check_feature_windows(vol_window, mom_window, rsi_window, volume_window)
    clean = validate_ohlcv(ohlcv)
    key = ("v1", _ohlcv_fingerprint(clean), int(vol_window), int(mom_window),
           int(rsi_window), int(volume_window), float(annualization))
    hit = _features_cache_get(key)
    if hit is not None:
        return hit
    base, _ = _base_from_clean(
        clean, vol_window=int(vol_window), mom_window=int(mom_window),
        rsi_window=int(rsi_window), volume_window=int(volume_window),
        annualization=int(annualization),
    )
    _features_cache_set(key, base)
    return base


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

    Single validation pass; the v1 RSI/log-return intermediates are reused
    (no second ewm pass). Memoized on identical inputs.
    """
    clean = validate_ohlcv(ohlcv)
    fingerprint = _ohlcv_fingerprint(clean)
    hit = _features_cache_get(("v2", fingerprint))
    if hit is not None:
        return hit
    base, shared = _base_from_clean(clean)
    # Warm the v1-default entry as well (same bytes, one build).
    _features_cache_set(("v1", fingerprint, 21, 21, 14, 20, 252.0), base)
    extra = _extended_from_clean(clean, lret=shared["lret"], rsi=shared["rsi"])
    joined = base.join(extra, how="inner")
    result = joined.dropna()
    _features_cache_set(("v2", fingerprint), result)
    return result


def build_feature_bundle(ohlcv: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Single-validate (v1, v2) bundle for the forecast hot path.

    Returns ``(base, extended)`` identical to calling :func:`build_features`
    (defaults) + :func:`build_extended_features` separately — same formulas,
    same warmup/dropna — with one validation pass and one shared RSI/ewm
    pass. Both frames are leakage-safe (past-only) like the individual
    builders.
    """
    clean = validate_ohlcv(ohlcv)
    fingerprint = _ohlcv_fingerprint(clean)
    v1_key = ("v1", fingerprint, 21, 21, 14, 20, 252.0)
    v2_key = ("v2", fingerprint)
    base_hit = _features_cache_get(v1_key)
    ext_hit = _features_cache_get(v2_key)
    if base_hit is not None and ext_hit is not None:
        return base_hit, ext_hit
    base, shared = _base_from_clean(clean)
    extra = _extended_from_clean(clean, lret=shared["lret"], rsi=shared["rsi"])
    extended = base.join(extra, how="inner").dropna()
    _features_cache_set(v1_key, base)
    _features_cache_set(v2_key, extended)
    return base, extended


def corporate_action_flags(
    close: Any,
    crash_drop: float = CORPORATE_ACTION_CRASH_DROP,
    jump_gain: float = CORPORATE_ACTION_JUMP_GAIN,
) -> dict[str, Any]:
    """Vectorized single-day corporate-action lineage screen (past data only).

    Flags days whose SIMPLE return looks like an unadjusted corporate
    action: ``ret <= crash_drop`` (e.g. -45%: an unadjusted 2:1 split
    prints as -50%) or ``ret >= jump_gain`` (e.g. +90%: an unadjusted 1:2
    reverse split prints as +100%). Bars are assumed adjusted upstream;
    this only reports lineage for the service-level confidence cap.

    Returns ``{has_crash_drop, has_jump, worst_drop, best_jump, n_obs,
    crash_drop_thresh, jump_gain_thresh}``. ``worst_drop``/``best_jump``
    are finite-only extremes (None when no finite move exists); the flags
    mirror the legacy ``pct_change().dropna()`` comparison, so non-finite
    moves (e.g. a zero close) still trip the guard instead of slipping
    through. Raises ValueError on empty input or bad thresholds.
    """
    try:
        crash = float(crash_drop)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"crash_drop must be in (-1, 0), got {crash_drop!r}") from exc
    if not -1.0 < crash < 0.0:
        raise ValueError(f"crash_drop must be in (-1, 0), got {crash_drop!r}")
    try:
        jump = float(jump_gain)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"jump_gain must be > 0, got {jump_gain!r}") from exc
    if not jump > 0:
        raise ValueError(f"jump_gain must be > 0, got {jump_gain!r}")
    prices = pd.Series(close, dtype=float)
    if len(prices.dropna()) < 2:
        raise ValueError("need >= 2 valid closes for corporate-action screen")
    with np.errstate(divide="ignore", invalid="ignore"):
        rets = prices.pct_change().dropna()
    if len(rets) == 0:
        raise ValueError("need >= 2 valid closes for corporate-action screen")
    raw = rets.to_numpy()
    finite = raw[np.isfinite(raw)]
    return {
        "has_crash_drop": bool((rets <= crash).any()),
        "has_jump": bool((rets >= jump).any()),
        "worst_drop": float(finite.min()) if len(finite) else None,
        "best_jump": float(finite.max()) if len(finite) else None,
        "n_obs": int(len(rets)),
        "crash_drop_thresh": crash,
        "jump_gain_thresh": jump,
    }


def thin_liquidity_flags(
    volume: Any,
    zero_frac_thresh: float = THIN_ZERO_VOLUME_FRAC,
) -> dict[str, Any]:
    """Vectorized thin-liquidity screen over trailing volumes (past only).

    Thin when the zero/non-positive-volume-bar fraction exceeds
    ``zero_frac_thresh`` (default 10%) or median volume is <= 0: estimates
    from non-trading stretches (halts, illiquid names, stub gaps) deserve
    less confidence. Returns ``{is_thin, zero_volume_frac, median_volume,
    n_obs, reason}`` (``reason`` is None when not thin). Raises ValueError
    on empty input or a bad threshold.
    """
    try:
        thresh = float(zero_frac_thresh)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"zero_frac_thresh must be in [0, 1], got {zero_frac_thresh!r}") from exc
    if not 0.0 <= thresh <= 1.0:
        raise ValueError(
            f"zero_frac_thresh must be in [0, 1], got {zero_frac_thresh!r}")
    vols = pd.Series(volume, dtype=float)
    valid = vols.dropna().to_numpy()
    valid = valid[np.isfinite(valid)]
    if len(valid) == 0:
        raise ValueError("need >= 1 valid volume observations")
    zero_frac = float((valid <= 0).mean())
    median_vol = float(np.median(valid))
    is_thin = bool(zero_frac > thresh or median_vol <= 0)
    reason = None
    if is_thin:
        if zero_frac > thresh:
            reason = (
                f"thin liquidity: {zero_frac:.1%} zero-volume bars "
                f"(> {thresh:.0%} gate)"
            )
        else:
            reason = "thin liquidity: median volume <= 0"
    return {
        "is_thin": is_thin,
        "zero_volume_frac": zero_frac,
        "median_volume": median_vol,
        "n_obs": int(len(valid)),
        "reason": reason,
    }


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
    "CORPORATE_ACTION_CRASH_DROP",
    "CORPORATE_ACTION_JUMP_GAIN",
    "THIN_ZERO_VOLUME_FRAC",
    "validate_ohlcv",
    "log_returns",
    "build_features",
    "build_extended_features",
    "build_feature_bundle",
    "clear_feature_cache",
    "corporate_action_flags",
    "thin_liquidity_flags",
    "future_return",
    "direction_label",
    "future_drawdown",
]
