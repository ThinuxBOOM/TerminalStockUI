"""Daily bar ingestion: yfinance -> price_bars (idempotent upsert).

Phase 1a: Vercel Cron (``GET /api/cron/ingest``) and
``scripts/backfill_bars.py`` both share :func:`ingest_symbols` here. This
module performs no HTTP itself; the FastAPI router and the CLI are thin
wrappers around it.

Idempotency: rows are keyed ``(instrument_id, timeframe, ts)``; re-runs
merge (update) instead of duplicating. Unknown symbols and per-symbol
fetch/DB failures are reported per symbol, never raised.
"""

from __future__ import annotations

import logging
import math
import os
from datetime import date, datetime, timezone

logger = logging.getLogger(__name__)

#: Default ingest universe (overridable via the ``INGEST_SYMBOLS`` env
#: comma-list). Canonical provider forms (Yahoo suffixes included).
DEFAULT_UNIVERSE: list[str] = [
    "AAPL",
    "MSFT",
    "JPM",
    "600519.SS",
    "MC.PA",
    "ASML.AS",
    "UCB.BR",
]

DEFAULT_TIMEFRAME = "1d"

#: Single yfinance history fetch per symbol (same Ticker.history pattern as
#: the quote provider's ``2d`` fetch, extended to daily bars).
FETCH_PERIOD = "2y"
FETCH_INTERVAL = "1d"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def default_universe() -> list[str]:
    """Ingest universe: ``INGEST_SYMBOLS`` comma-list wins, else default."""
    raw = (os.getenv("INGEST_SYMBOLS", "") or "").strip()
    if raw:
        out: list[str] = []
        seen: set[str] = set()
        for part in raw.split(","):
            item = part.strip()
            if item and item.upper() not in seen:
                seen.add(item.upper())
                out.append(item)
        if out:
            return out
    return list(DEFAULT_UNIVERSE)


def _fnum(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN / pandas NA guard without importing pandas
        return None
    if not math.isfinite(number):  # inf leaks into the DB/JSON otherwise
        return None
    return number


def _inum(value: object) -> int | None:
    number = _fnum(value)
    if number is None:
        return None
    try:
        return int(number)
    except (TypeError, ValueError, OverflowError):
        return None


def _index_to_utc(idx: object) -> datetime | None:
    """Normalize a yfinance history index entry to an aware UTC datetime."""
    try:
        if hasattr(idx, "to_pydatetime"):
            moment = idx.to_pydatetime()  # type: ignore[union-attr]
        elif isinstance(idx, datetime):
            moment = idx
        elif isinstance(idx, date):
            moment = datetime(idx.year, idx.month, idx.day)
        else:
            moment = datetime.fromisoformat(str(idx))
    except Exception:
        return None
    if not isinstance(moment, datetime):
        return None
    try:
        return _ensure_utc(moment)
    except Exception:
        return None


def fetch_daily_bars(
    provider_symbol: str,
    period: str = FETCH_PERIOD,
    interval: str = FETCH_INTERVAL,
) -> list[dict]:
    """Fetch daily bars for one provider symbol via yfinance (lazy import).

    Single ``Ticker.history(period="2y", interval="1d")`` call. Returns
    ascending ``[{ts (aware UTC), open, high, low, close, volume}]``.
    Raises on missing package / empty history / no usable rows; callers
    convert that into a per-symbol error entry (never a batch 500).
    """
    symbol = (provider_symbol or "").strip().upper()
    if not symbol:
        raise ValueError("empty symbol")
    try:
        import yfinance as yf
    except Exception as exc:
        raise RuntimeError("yfinance package unavailable") from exc
    ticker = yf.Ticker(symbol)
    hist = ticker.history(period=period, interval=interval, auto_adjust=True)
    if hist is None or len(hist) == 0:
        raise RuntimeError(f"no data for {symbol}")
    try:  # single-ticker fetches are flat; flatten MultiIndex just in case
        import pandas as pd

        if isinstance(hist.columns, pd.MultiIndex):
            hist.columns = hist.columns.get_level_values(0)
    except Exception:
        pass
    colmap = {str(col).strip().lower(): col for col in list(hist.columns)}

    def _col(*names: str):  # first matching physical column or None
        for name in names:
            if name in colmap:
                return colmap[name]
        return None

    c_open = _col("open")
    c_high = _col("high")
    c_low = _col("low")
    c_close = _col("close", "adj close")
    c_vol = _col("volume", "vol")
    if c_close is None:
        raise RuntimeError(f"no close column for {symbol}")
    bars: list[dict] = []
    for idx, row in hist.iterrows():
        ts = _index_to_utc(idx)
        if ts is None:
            continue
        close = _fnum(row[c_close])
        if close is None:
            continue
        bars.append({
            "ts": ts,
            "open": _fnum(row[c_open]) if c_open is not None else None,
            "high": _fnum(row[c_high]) if c_high is not None else None,
            "low": _fnum(row[c_low]) if c_low is not None else None,
            "close": close,
            "volume": _inum(row[c_vol]) if c_vol is not None else None,
        })
    bars.sort(key=lambda item: item["ts"])
    if not bars:
        raise RuntimeError(f"no usable bars for {symbol}")
    return bars


def _grade_for_ingest() -> str:
    try:
        from backend.market_data.quality import grade_quality
    except Exception:
        try:
            from .quality import grade_quality  # type: ignore[no-redef]
        except Exception:
            return "B"
    try:
        grade, _ = grade_quality(
            delay_minutes=15, age_minutes=0.0, missing_fields=[],
            fallback_used=False, reconciled=False,
        )
        return grade
    except Exception:
        return "B"


def _get_or_create_db_instrument(db, registry_instrument):
    """Map a registry instrument to its DB row (create on first ingest)."""
    from backend.db.models import Instrument as DBInstrument

    row = (
        db.query(DBInstrument)
        .filter(
            DBInstrument.exchange_mic == registry_instrument.exchange_mic,
            DBInstrument.exchange_symbol == registry_instrument.exchange_symbol,
        )
        .first()
    )
    if row is not None:
        return row
    row = DBInstrument(
        exchange_mic=registry_instrument.exchange_mic,
        exchange_symbol=registry_instrument.exchange_symbol,
        provider_symbol=registry_instrument.provider_symbol
        or registry_instrument.exchange_symbol,
        company_name=registry_instrument.company_name
        or registry_instrument.exchange_symbol,
        currency=registry_instrument.currency or "USD",
        country=registry_instrument.country,
        sector=registry_instrument.sector,
        timezone=registry_instrument.timezone or "UTC",
        trading_calendar=registry_instrument.trading_calendar
        or registry_instrument.exchange_mic,
        is_active=True,
    )
    db.add(row)
    db.flush()  # assign the UUID PK before bar upserts
    return row


def _upsert_bars(db, db_instrument, bars: list[dict], *, timeframe: str) -> int:
    """Idempotent upsert keyed (instrument_id, timeframe, ts). Returns count."""
    from backend.db.models import PriceBar

    now = _utcnow()
    grade = _grade_for_ingest()
    count = 0
    for item in bars or []:
        ts = item.get("ts")
        if not isinstance(ts, datetime):
            continue
        try:
            ts_utc = _ensure_utc(ts)
        except Exception:
            continue
        db.merge(PriceBar(
            instrument_id=db_instrument.instrument_id,
            ts=ts_utc,
            timeframe=timeframe,
            open=item.get("open"),
            high=item.get("high"),
            low=item.get("low"),
            close=item.get("close"),
            volume=item.get("volume"),
            source="yfinance",
            as_of=now,
            quality_grade=grade,
        ))
        count += 1
    return count


def ingest_symbols(
    symbols: list[str] | tuple[str, ...] | None,
    *,
    db_url: str | None = None,
    registry=None,
    fetch_fn=None,
    timeframe: str = DEFAULT_TIMEFRAME,
) -> tuple[dict[str, int], dict[str, str]]:
    """Ingest daily bars for ``symbols`` into ``price_bars`` (upsert).

    Returns ``(ingested, errors)`` where ``ingested`` maps the canonical
    provider symbol to the upserted bar count and ``errors`` maps the raw
    input symbol to a short failure reason. Never raises for per-symbol
    problems (unknown instrument, fetch failure, row failure) or for an
    unreachable DB (all symbols then land in ``errors``).
    """
    if registry is None:
        from backend.instruments.registry import InstrumentRegistry

        registry = InstrumentRegistry()
    wanted: list[str] = []
    for raw in symbols or []:
        text = (raw or "").strip()
        if text:
            wanted.append(text)
    if not wanted:
        return {}, {}
    fetch = fetch_fn if fetch_fn is not None else fetch_daily_bars

    from backend.db.session import get_session_factory, init_db

    try:
        if db_url is not None:
            init_db(db_url)
            Session = get_session_factory(db_url)
        else:
            init_db()
            Session = get_session_factory()
        db = Session()
    except Exception:
        # DB unreachable: report per symbol, never 500 the batch. The
        # exception text is deliberately reduced to its type so connection
        # strings can never leak into responses or logs.
        logger.warning("ingest db unavailable symbols=%d", len(wanted))
        return {}, {raw: "db unavailable" for raw in wanted}

    ingested: dict[str, int] = {}
    errors: dict[str, str] = {}
    try:
        for raw in wanted:
            try:
                instrument, _, _ = registry.resolve(raw)
                if instrument is None:
                    errors[raw] = f"unknown instrument for {raw!r}"
                    continue
                provider_symbol = (
                    instrument.provider_symbol or raw.strip().upper()
                )
                bars = fetch(provider_symbol)
                db_inst = _get_or_create_db_instrument(db, instrument)
                count = _upsert_bars(db, db_inst, bars, timeframe=timeframe)
                db.commit()
                ingested[provider_symbol] = count
            except Exception as exc:
                try:
                    db.rollback()
                except Exception:
                    pass
                reason = f"{type(exc).__name__}: {str(exc)[:200]}"
                errors[raw] = reason
                logger.warning("ingest symbol failed symbols=1")
    finally:
        try:
            db.close()
        except Exception:
            pass
    logger.info(
        "ingest done ingested=%d errors=%d", len(ingested), len(errors)
    )
    return ingested, errors
