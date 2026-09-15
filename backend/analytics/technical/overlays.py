"""Chart overlay indicator engine (Backend Agent 5, repaired).

Pure, vectorized, deterministic series builders for the PriceChart overlays.
No network, no randomness, no wall-clock reads; identical inputs produce
identical outputs.

Frontend contract (read-only alignment, never edited here):
  * ``frontend/src/api/client.js`` ``SUPPORTED_INDICATORS`` +
    ``normalizeIndicators()`` expects full point series under an
    ``indicators`` map: ``SMA20 -> [{time, value}]``,
    ``BB20 -> {upper, middle, lower}``, ``MACD -> {macd, signal, histogram}``.
  * ``frontend/src/features/security/PriceChart.jsx`` documents
    ``GET /api/analytics/{symbol}?indicators=SMA20,EMA12,RSI14,MACD,BB20,VWAP,ATR14``.

Conventions (match ``indicators.py`` where overlapping):
  * Time-series outputs preserve input order; leading warmup rows are
    ``None`` (null on the wire), never ``0``.
  * Insufficient history or missing fields -> per-indicator
    ``{"status": "unavailable", "reason": ...}`` (never raises, never
    zero-filled).
  * ``compute_indicators`` returns the ``{requested, bars, max_points,
    series}`` wrapper the API contract requires, PLUS flat duplicates of
    each series entry at the top level so the existing frontend
    ``normalizeIndicators()`` passthrough keeps working without a
    frontend change (it ignores ``requested/bars/max_points/series/
    provenance`` keys and keeps canonical series keys).
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

SUPPORTED_INDICATORS: list[str] = [
    "SMA20",
    "SMA50",
    "SMA200",
    "EMA12",
    "EMA26",
    "RSI14",
    "MACD",
    "BB20",
    "VWAP",
    "ATR14",
    "VOLUME_SMA20",
]

# Normalized token (upper, no spaces/_/-) -> canonical.
_ALIASES: dict[str, str] = {
    "SMA20": "SMA20",
    "SMA50": "SMA50",
    "SMA200": "SMA200",
    "EMA12": "EMA12",
    "EMA26": "EMA26",
    "RSI14": "RSI14",
    "RSI": "RSI14",
    "MACD": "MACD",
    "BB20": "BB20",
    "BB": "BB20",
    "BOLLINGER": "BB20",
    "BOLLINGER20": "BB20",
    "BBANDS": "BB20",
    "BBANDS20": "BB20",
    "BOLLINGERBANDS": "BB20",
    "BOLLINGERBANDS20": "BB20",
    "VWAP": "VWAP",
    "ATR14": "ATR14",
    "ATR": "ATR14",
    "VOLUMESMA20": "VOLUME_SMA20",
    "VOLSMA20": "VOLUME_SMA20",
    "VOLUMESMA": "VOLUME_SMA20",
    "VOLSMA": "VOLUME_SMA20",
    "VOLUME": "VOLUME_SMA20",
    "VOL": "VOLUME_SMA20",
    "VOLUME20": "VOLUME_SMA20",
    "VOL20": "VOLUME_SMA20",
}

_WS_RE = re.compile(r"[\s_\-]+")

# Minimum bars required per canonical indicator (matches indicators.py
# min_length rules where applicable).
_MIN_BARS: dict[str, int] = {
    "SMA20": 20,
    "SMA50": 50,
    "SMA200": 200,
    "EMA12": 12,
    "EMA26": 26,
    "RSI14": 15,
    "MACD": 35,  # slow(26) + signal(9), mirrors indicators.macd
    "BB20": 20,
    "VWAP": 1,
    "ATR14": 15,
    "VOLUME_SMA20": 20,
}


def _normalize_token(raw: Any) -> str | None:
    try:
        text = str(raw if raw is not None else "").strip().upper()
    except Exception:
        return None
    if not text:
        return None
    key = _WS_RE.sub("", text)
    return _ALIASES.get(key)


def parse_indicators(value: Any) -> list[str]:
    """Parse a comma-separated string or list into canonical indicator names.

    Dedupes preserving first-seen order. Empty/None -> []. Unknown tokens
    raise ``ValueError`` with the exact wire detail the APIs surface as 422::

        unknown indicator(s): FOO; expected one of ['SMA20', ...]
    """
    if value is None:
        return []
    if isinstance(value, str):
        tokens = value.split(",")
    elif isinstance(value, (list, tuple)):
        tokens = list(value)
    else:
        tokens = [value]
    out: list[str] = []
    seen: set[str] = set()
    unknown: list[str] = []
    for tok in tokens:
        try:
            text = str(tok).strip() if tok is not None else ""
        except Exception:
            text = ""
        if not text:
            continue
        canon = _normalize_token(text)
        if canon is None:
            unknown.append(text)
        elif canon not in seen:
            seen.add(canon)
            out.append(canon)
    if unknown:
        raise ValueError(
            f"unknown indicator(s): {', '.join(unknown)}; "
            f"expected one of {SUPPORTED_INDICATORS}"
        )
    return out


def _unavailable(reason: str) -> dict:
    return {"status": "unavailable", "reason": reason}


def _to_frame(bars: Any) -> pd.DataFrame | None:
    """Coerce bars (DataFrame | list[dict] | {bars:[...]}) to OHLCV frame."""
    try:
        if isinstance(bars, dict) and isinstance(bars.get("bars"), list):
            rows = bars["bars"]
            frame = pd.DataFrame(rows)
        elif isinstance(bars, pd.DataFrame):
            frame = bars.copy()
        elif isinstance(bars, (list, tuple)):
            if not bars:
                return None
            frame = pd.DataFrame(list(bars))
        else:
            return None
        if frame is None or len(frame) == 0:
            return None
        # Normalize column names to lower case.
        try:
            frame.columns = [str(c).lower() for c in frame.columns]
        except Exception:
            pass
        # Resolve a datetime index from index or ts/time/date column.
        idx = None
        try:
            if isinstance(frame.index, pd.DatetimeIndex):
                idx = pd.to_datetime(frame.index, errors="coerce")
            for key in ("ts", "time", "date"):
                if key in frame.columns:
                    cand = pd.to_datetime(frame[key], errors="coerce", utc=True)
                    if cand.notna().any():
                        idx = cand
                        break
        except Exception:
            idx = None
        if idx is not None:
            try:
                frame.index = pd.DatetimeIndex(idx)
                # Drop rows with unparseable time (honest: no fabricated ts).
                frame = frame[frame.index.notna()]
                frame = frame.sort_index(kind="mergesort")
            except Exception:
                pass
        # Coerce OHLCV to numeric (non-numeric -> NaN, never raises).
        for col in ("open", "high", "low", "close", "volume"):
            if col in frame.columns:
                try:
                    frame[col] = pd.to_numeric(frame[col], errors="coerce").astype(float)
                except Exception:
                    pass
        if len(frame) == 0:
            return None
        return frame
    except Exception:
        return None


def _format_time(ts: Any) -> str | None:
    try:
        dt = pd.to_datetime(ts, errors="coerce", utc=True)
        if pd.isna(dt):
            return None
        return str(dt.date().isoformat())
    except Exception:
        return None


def _clean_number(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def _format_index(index: Any) -> list[str | None]:
    """Vectorized index -> YYYY-MM-DD strings (one to_datetime, not N)."""
    try:
        dt = pd.to_datetime(index, errors="coerce", utc=True)
        out: list[str | None] = []
        try:
            items = list(dt)
        except Exception:
            return [_format_time(t) for t in list(index)]
        for ts in items:
            try:
                if pd.isna(ts):
                    out.append(None)
                else:
                    out.append(ts.date().isoformat())
            except Exception:
                out.append(None)
        return out
    except Exception:
        try:
            return [_format_time(t) for t in list(index)]
        except Exception:
            return []


def _points_with_times(times: list[str | None], values: Any, max_points: int) -> list[dict]:
    """Build [{time, value}] from precomputed time strings (fast path)."""
    try:
        n = int(max_points)
    except (TypeError, ValueError):
        n = 1000
    if n < 1:
        n = 1000
    try:
        val_list = list(values)
    except Exception:
        return []
    total = min(len(times), len(val_list))
    start = max(0, total - n)
    out: list[dict] = []
    for i in range(start, total):
        t = times[i]
        if not t:
            continue
        out.append({"time": t, "value": _clean_number(val_list[i])})
    # Dedupe on time (last wins), keep ascending — mirrors frontend.
    deduped: dict[str, float | None] = {}
    order: list[str] = []
    for p in out:
        if p["time"] not in deduped:
            order.append(p["time"])
        deduped[p["time"]] = p["value"]
    # order already ascending (frame sorted); re-emit preserving last-wins.
    # Re-sort to be deterministic even if input had dupes out of order.
    return [{"time": t, "value": deduped[t]} for t in sorted(deduped)]


def _points(index: Any, values: Any, max_points: int) -> list[dict]:
    """Back-compat wrapper: format index then build points (slower)."""
    try:
        times = _format_index(index)
    except Exception:
        times = []
    return _points_with_times(times, values, max_points)


def _sma(values: pd.Series, window: int) -> pd.Series:
    return values.rolling(window=window, min_periods=window).mean()


def _ema(values: pd.Series, window: int) -> pd.Series:
    return values.ewm(span=window, adjust=False, min_periods=window).mean()


def _rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()
    rs = avg_gain / avg_loss
    out = 100.0 - (100.0 / (1.0 + rs))
    out = out.mask((avg_loss == 0) & (avg_gain > 0), 100.0)
    out = out.mask((avg_loss == 0) & (avg_gain == 0), 50.0)
    return out


def _macd_frame(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    ef = close.ewm(span=fast, adjust=False, min_periods=fast).mean()
    es = close.ewm(span=slow, adjust=False, min_periods=slow).mean()
    line = ef - es
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame({"macd": line, "signal": sig, "histogram": line - sig})


def _bb_frame(close: pd.Series, window: int = 20, num_std: float = 2.0) -> pd.DataFrame:
    mid = close.rolling(window=window, min_periods=window).mean()
    std = close.rolling(window=window, min_periods=window).std(ddof=0)
    upper = mid + float(num_std) * std
    lower = mid - float(num_std) * std
    return pd.DataFrame({"upper": upper, "middle": mid, "lower": lower})


def _atr_series(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    prev = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev).abs(), (low - prev).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()


def _vwap_series(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series) -> pd.Series:
    typical = (high + low + close) / 3.0
    pv = typical * volume
    cum_pv = pv.cumsum()
    cum_v = volume.cumsum()
    out = cum_pv / cum_v
    # cum_v == 0 -> inf/NaN -> null downstream; mask explicitly.
    try:
        out = out.mask(cum_v == 0)
        out = out.replace([float("inf"), float("-inf")], float("nan"))
    except Exception:
        pass
    return out


def compute_indicators(bars: Any, requested: Any, max_points: int = 1000) -> dict:
    """Compute overlay series for ``requested`` indicators over ``bars``.

    Args:
        bars: DataFrame with OHLCV (+ datetime index or ts/time/date
            column), list of OHLCV dicts, or ``{bars: [...]}`` service
            payload. Never raises on bad input (unavailable per indicator).
        requested: canonical list, comma string, or single name. Unknown
            names raise ``ValueError`` with the 422 detail string.
        max_points: cap per-series points to the last N bars (default 1000).

    Returns:
        Dict with ``requested/bars/max_points/series`` (the API wire
        wrapper) PLUS flat duplicates of each series entry at the top
        level for the existing frontend passthrough. ``series`` is the
        source of truth; flat keys are the same objects. Unavailable
        indicators map to ``{"status": "unavailable", "reason": ...}``.
    """
    try:
        n_points = int(max_points)
    except (TypeError, ValueError):
        n_points = 1000
    if n_points < 1:
        n_points = 1000

    if isinstance(requested, str):
        wanted = parse_indicators(requested)
    elif isinstance(requested, (list, tuple)):
        # Normalize each entry (supports aliases); reuse parse for errors.
        wanted = parse_indicators(list(requested))
    elif requested is None:
        wanted = []
    else:
        wanted = parse_indicators([requested])

    frame = _to_frame(bars)
    n_bars = int(len(frame)) if frame is not None else 0

    series: dict[str, Any] = {}
    if not wanted:
        out: dict[str, Any] = {
            "requested": [],
            "bars": n_bars,
            "max_points": n_points,
            "series": series,
        }
        return out

    if frame is None or n_bars == 0:
        for name in wanted:
            series[name] = _unavailable("no bars: empty price history")
    else:
        close = frame["close"] if "close" in frame.columns else None
        high = frame["high"] if "high" in frame.columns else None
        low = frame["low"] if "low" in frame.columns else None
        volume = frame["volume"] if "volume" in frame.columns else None
        times = _format_index(frame.index)
        for name in wanted:
            need = _MIN_BARS.get(name, 1)
            if n_bars < need:
                series[name] = _unavailable(
                    f"insufficient history for {name}: {n_bars} bars, need >= {need}"
                )
                continue
            try:
                if name in ("SMA20", "SMA50", "SMA200"):
                    if close is None:
                        series[name] = _unavailable("missing required field 'close'")
                        continue
                    w = {"SMA20": 20, "SMA50": 50, "SMA200": 200}[name]
                    vals = _sma(close, w)
                    series[name] = _points_with_times(times, vals, n_points)
                elif name in ("EMA12", "EMA26"):
                    if close is None:
                        series[name] = _unavailable("missing required field 'close'")
                        continue
                    w = 12 if name == "EMA12" else 26
                    vals = _ema(close, w)
                    series[name] = _points_with_times(times, vals, n_points)
                elif name == "RSI14":
                    if close is None:
                        series[name] = _unavailable("missing required field 'close'")
                        continue
                    vals = _rsi(close, 14)
                    series[name] = _points_with_times(times, vals, n_points)
                elif name == "MACD":
                    if close is None:
                        series[name] = _unavailable("missing required field 'close'")
                        continue
                    fr = _macd_frame(close)
                    series[name] = {
                        "macd": _points_with_times(times, fr["macd"], n_points),
                        "signal": _points_with_times(times, fr["signal"], n_points),
                        "histogram": _points_with_times(times, fr["histogram"], n_points),
                    }
                elif name == "BB20":
                    if close is None:
                        series[name] = _unavailable("missing required field 'close'")
                        continue
                    fr = _bb_frame(close, 20, 2.0)
                    series[name] = {
                        "upper": _points_with_times(times, fr["upper"], n_points),
                        "middle": _points_with_times(times, fr["middle"], n_points),
                        "lower": _points_with_times(times, fr["lower"], n_points),
                    }
                elif name == "VWAP":
                    if high is None or low is None or close is None or volume is None:
                        series[name] = _unavailable(
                            "missing required field(s) for VWAP: need high, low, close, volume"
                        )
                        continue
                    vals = _vwap_series(high, low, close, volume)
                    if vals.dropna().empty:
                        series[name] = _unavailable("VWAP unavailable: no valid volume-weighted prices")
                        continue
                    series[name] = _points_with_times(times, vals, n_points)
                elif name == "ATR14":
                    if high is None or low is None or close is None:
                        series[name] = _unavailable(
                            "missing required field(s) for ATR14: need high, low, close"
                        )
                        continue
                    vals = _atr_series(high, low, close, 14)
                    series[name] = _points_with_times(times, vals, n_points)
                elif name == "VOLUME_SMA20":
                    if volume is None:
                        series[name] = _unavailable("missing required field 'volume'")
                        continue
                    vals = _sma(volume, 20)
                    series[name] = _points_with_times(times, vals, n_points)
                else:  # pragma: no cover - parse guarantees canonical
                    series[name] = _unavailable(f"unsupported indicator {name!r}")
            except Exception as exc:
                series[name] = _unavailable(f"{name} failed: {exc}")

    out = {
        "requested": list(wanted),
        "bars": n_bars,
        "max_points": n_points,
        "series": series,
    }
    # Flat duplicates for frontend normalizeIndicators() passthrough.
    for k, v in series.items():
        out[k] = v
    return out


__all__ = [
    "SUPPORTED_INDICATORS",
    "parse_indicators",
    "compute_indicators",
]
