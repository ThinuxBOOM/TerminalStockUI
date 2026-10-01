"""Training history: bulk daily bars from yfinance, cached on disk.

Training needs ~10 years for hundreds of symbols. That history is downloaded
at training time instead of being stored in Postgres (it would dwarf the
rest of the database); only the fitted model is persisted.
"""

from __future__ import annotations

import logging
import pickle
import time
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

#: Benchmarks used as the "market" for each venue's features.
MARKET_PROXY = {"US": "SPY", "XSHG": "000001.SS", "XPAR": "^FCHI", "XAMS": "^AEX", "XBRU": "^BFX"}
OHLCV = ["open", "high", "low", "close", "volume"]


def _download_chunk(symbols: list[str], period: str) -> dict[str, pd.DataFrame]:
    import yfinance as yf

    raw = yf.download(
        symbols, period=period, interval="1d", auto_adjust=True, group_by="ticker",
        threads=True, progress=False,
    )
    out: dict[str, pd.DataFrame] = {}
    if raw is None or raw.empty:
        return out
    for sym in symbols:
        try:
            frame = raw[sym] if isinstance(raw.columns, pd.MultiIndex) else raw
        except KeyError:
            continue
        frame = frame.rename(columns=str.lower)[[c for c in OHLCV if c in frame.columns.str.lower()]]
        frame = frame.dropna(subset=["close"])
        if len(frame) == 0:
            continue
        idx = pd.DatetimeIndex(frame.index)
        frame.index = idx.tz_localize(None) if idx.tz is not None else idx
        out[sym] = frame.astype(float)
    return out


def download_history(
    symbols: list[str], *, period: str = "10y", chunk: int = 60, retries: int = 2,
) -> dict[str, pd.DataFrame]:
    """Daily adjusted OHLCV per symbol (naive dates, exchange-local sessions)."""
    frames: dict[str, pd.DataFrame] = {}
    todo = sorted(set(symbols))
    for start in range(0, len(todo), chunk):
        batch = todo[start:start + chunk]
        for attempt in range(retries + 1):
            try:
                got = _download_chunk(batch, period)
                frames.update(got)
                missing = [s for s in batch if s not in got]
                if missing:
                    log.info("no history for %d symbols: %s", len(missing), ", ".join(missing[:10]))
                break
            except Exception as exc:  # network: retry the chunk, then move on
                log.warning("download chunk %d failed (%s), attempt %d", start, exc, attempt + 1)
                time.sleep(2 * (attempt + 1))
    return frames


def load_or_download(
    symbols: list[str], cache: Path, *, period: str = "10y", max_age_hours: float = 20.0,
) -> dict[str, pd.DataFrame]:
    """``download_history`` with a pickle cache (refreshed when older than max_age_hours)."""
    if cache.exists() and (time.time() - cache.stat().st_mtime) < max_age_hours * 3600:
        with cache.open("rb") as fh:
            frames = pickle.load(fh)
        if set(symbols) <= set(frames) | set(frames.get("__missing__", [])):
            return {k: v for k, v in frames.items() if k != "__missing__"}
    frames = download_history(symbols, period=period)
    cache.parent.mkdir(parents=True, exist_ok=True)
    with cache.open("wb") as fh:
        pickle.dump({**frames, "__missing__": [s for s in symbols if s not in frames]}, fh)
    return frames


def us_universe() -> list[str]:
    """S&P 500 members as yfinance symbols (BRK.B -> BRK-B)."""
    from backend.instruments.sp500 import SP500_MEMBERS

    return sorted({sym.replace(".", "-") for sym, *_ in SP500_MEMBERS})


def sectors() -> dict[str, str]:
    from backend.instruments.sp500 import SP500_MEMBERS

    return {sym.replace(".", "-"): sector for sym, _name, sector, _mic in SP500_MEMBERS}
