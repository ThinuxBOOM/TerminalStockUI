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
    # US majors (registry seeds; screener/liquidity breadth needs them warm).
    "AAPL",
    "MSFT",
    "NVDA",
    "TSLA",
    "GOOGL",
    "AMZN",
    "META",
    "AVGO",
    "AAP",
    "JPM",
    # SSE seeds (Top-20 shows 5 rows; all must be warm).
    "600519.SS",
    "600000.SS",
    "600036.SS",
    "601318.SS",
    "600900.SS",
    # Euronext seeds.
    "MC.PA",
    "ACA.PA",
    "OR.PA",
    "ASML.AS",
    "INGA.AS",
    "UCB.BR",
    "ABI.BR",
    # Index proxies (GET /api/markets/{mic}/index serves from DB; without
    # these every index card pays a live 2y Yahoo fetch from serverless and
    # 502s under throttle. US ETFs first: Euronext tries EWQ/EWN before
    # CAC.PA/IAEX.AS per BENCHMARKS order.)
    "SPY",
    "QQQ",
    "000001.SS",
    "EWQ",
    "EWN",
    "EWK",
    "CAC.PA",
    "IAEX.AS",
]

DEFAULT_TIMEFRAME = "1d"

#: Bar-chain order for daily ingestion (overridable via ``INGEST_BAR_CHAIN``
#: comma-list, e.g. ``yfinance``). Without Alpaca keys this stays
#: ``["yfinance"]``. With keys, ``bar_chain()`` prepends ``alpaca``
#: so Alpaca-covered (US) symbols ingest from the same feed as their quotes
#: (no Alpaca-quote vs yfinance-chart mismatch). Unknown names are ignored
#: so a typo never breaks a cron tick. (stooq dropped: upstream retired its
#: keyless quote endpoint — every stooq fetch 404s.)
DEFAULT_BAR_CHAIN: list[str] = ["yfinance"]


def _alpaca_keys_present() -> bool:
    """True when Alpaca key id + secret resolve (env/args). No network."""
    try:
        from backend.market_data.providers.alpaca import resolve_keys
    except Exception:
        try:
            from .providers.alpaca import resolve_keys  # type: ignore[no-redef]
        except Exception:
            return False
    try:
        key, secret = resolve_keys()
    except Exception:
        return False
    return bool(key and secret)

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


def _local_tz_for_symbol(symbol_hint: str) -> str:
    sym = (symbol_hint or "").strip().upper()
    if sym.endswith(".SS"):
        return "Asia/Shanghai"
    if sym.endswith(".PA"):
        return "Europe/Paris"
    if sym.endswith(".AS"):
        return "Europe/Amsterdam"
    if sym.endswith(".BR"):
        return "Europe/Brussels"
    if sym.endswith(".BO"):
        return "Asia/Colombo"
    if sym.endswith(".L"):
        return "Europe/London"
    if sym.endswith((".CN", ".FR", ".NL", ".BE")):
        return {
            ".CN": "Asia/Shanghai",
            ".FR": "Europe/Paris",
            ".NL": "Europe/Amsterdam",
            ".BE": "Europe/Brussels",
        }[sym[-3:]]
    return "America/New_York"


def default_universe() -> list[str]:
    """Ingest universe: ``INGEST_SYMBOLS`` comma-list wins, else default."""
    raw = (os.getenv("INGEST_SYMBOLS", "") or "").strip()
    if raw:
        out: list[str] = []
        seen: set[str] = set()
        for part in raw.split(","):
            item = part.strip()
            if not item:
                continue
            # Validate + cap: an unbounded env list guarantees a serverless
            # timeout (each symbol pays a live Yahoo fetch). Cap at 25.
            try:
                from backend.security.validation import validate_symbol as _vsym
                _vsym(item, field="symbol")
            except Exception:
                logger.warning("ingest: skipping invalid INGEST_SYMBOLS entry %r", item[:32])
                continue
            if item.upper() not in seen:
                seen.add(item.upper())
                out.append(item)
            if len(out) >= 25:
                logger.warning("ingest: INGEST_SYMBOLS capped at 25 entries")
                break
        if out:
            return out
    return list(DEFAULT_UNIVERSE)


def bar_chain() -> list[str]:
    """Ordered fetch fallbacks for daily bars (env ``INGEST_BAR_CHAIN``).

    Explicit env wins (alpaca/yfinance names accepted; legacy "stooq" entries
    are ignored). Otherwise the default prepends ``alpaca`` when Alpaca keys
    resolve (same-feed bars for Alpaca-covered US symbols, matching their
    quotes) and stays ``["yfinance"]`` when unconfigured (alpaca would fail
    fast without network, so omitting it also keeps cron logs quiet).
    """
    raw = (os.getenv("INGEST_BAR_CHAIN", "") or "").strip().lower()
    if raw:
        out: list[str] = []
        for part in raw.split(","):
            name = part.strip()
            if name in ("alpaca", "yfinance") and name not in out:
                out.append(name)
        if out:
            return out
    if _alpaca_keys_present():
        return ["alpaca", "yfinance"]
    return list(DEFAULT_BAR_CHAIN)


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


def _index_to_utc(idx: object, symbol_hint: str = "") -> datetime | None:
    """Normalize a yfinance history index entry to an aware UTC datetime.

    yfinance bars are exchange-local. Naive midnight entries are localized
    via the symbol hint (.SS->Asia/Shanghai, .PA/.AS/.BR->Europe/*, else
    America/New_York) before converting to UTC, so XSHG midnight no longer
    stamps as UTC midnight (8h shift).
    """
    try:
        if hasattr(idx, "to_pydatetime"):
            moment = idx.to_pydatetime()  # type: ignore[union-attr]
        elif isinstance(idx, datetime):
            moment = idx
        elif isinstance(idx, date):
            moment = datetime(idx.year, idx.month, idx.day)
        else:
            text = str(idx).strip()
            # Vendor-string normalization: 'Z' suffix + nanosecond fractions
            # (Alpaca emits 9-digit fractions; fromisoformat takes 3 or 6 —
            # without this every Alpaca bar row is dropped as unparseable).
            # Inputs without fractions/suffixes pass through byte-identical.
            if text.endswith(("Z", "z")):
                text = text[:-1] + "+00:00"
            try:
                import re as _re

                text = _re.sub(r"(\.\d{6})\d+(?=[+-]\d{2}:?\d{2}$|$)", r"\1", text)
            except Exception:
                pass
            moment = datetime.fromisoformat(text)
    except Exception:
        return None
    if not isinstance(moment, datetime):
        return None
    try:
        if moment.tzinfo is None:
            # Exchange-local naive midnight -> localize before UTC convert.
            try:
                from zoneinfo import ZoneInfo
                tz = ZoneInfo(_local_tz_for_symbol(symbol_hint))
                moment = moment.replace(tzinfo=tz)
            except Exception:
                pass
        return _ensure_utc(moment)
    except Exception:
        return None


def fetch_daily_bars(
    provider_symbol: str,
    period: str = FETCH_PERIOD,
    interval: str = FETCH_INTERVAL,
    timeout: float = 12.0,
) -> list[dict]:
    """Fetch daily bars for one provider symbol via yfinance (lazy import).

    Single ``Ticker.history(period="2y", interval="1d")`` call. Returns
    ascending ``[{ts (aware UTC), open, high, low, close, volume}]``.
    Raises on missing package / empty history / no usable rows; callers
    convert that into a per-symbol error entry (never a batch 500).

    Timeout: yfinance has no global timeout — the history call runs in a
    worker thread and is joined with ``timeout`` (default 12s, well under
    the Vercel ``maxDuration``) so a hung Yahoo fetch never holds a
    serverless invocation to its 60s kill.
    """
    symbol = (provider_symbol or "").strip().upper()
    if not symbol:
        raise ValueError("empty symbol")
    # Yahoo class-share form uses a dash (BRK-B); users type BRK.B. The
    # quote chain normalizes per provider, but bars fetch a single string —
    # map dotted bare tickers to dash form unless the dot starts a known
    # venue suffix (.SS/.PA/.AS/.BR/...). Suffix forms pass through intact.
    try:
        if "." in symbol and not symbol.endswith(
            (".SS", ".PA", ".AS", ".BR", ".CN", ".FR", ".NL", ".BE", ".L")
        ):
            symbol = symbol.replace(".", "-")
    except Exception:
        pass
    try:
        import yfinance as yf
    except Exception as exc:
        raise RuntimeError("yfinance package unavailable") from exc
    from concurrent.futures import ThreadPoolExecutor as _TPE

    def _do_history():
        ticker = yf.Ticker(symbol)
        # yfinance >=0.2.40 accepts timeout=; older versions ignore **kwargs
        # via curl — try with timeout first, fall back without.
        try:
            return ticker.history(period=period, interval=interval, auto_adjust=True, timeout=10)
        except TypeError:
            return ticker.history(period=period, interval=interval, auto_adjust=True)

    try:
        budget = max(3.0, min(float(timeout), 25.0))
    except (TypeError, ValueError):
        budget = 12.0
    try:
        with _TPE(max_workers=1) as _pool:
            _fut = _pool.submit(_do_history)
            hist = _fut.result(timeout=budget)
    except Exception as exc:
        raise RuntimeError(f"yahoo fetch timed out/failed for {symbol}: {type(exc).__name__}") from exc
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
        ts = _index_to_utc(idx, symbol_hint=symbol)
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


def fetch_stooq_daily_bars(
    provider_symbol: str,
    period: str = FETCH_PERIOD,
    interval: str = FETCH_INTERVAL,
) -> list[dict]:
    """Best-effort daily bars via Stooq CSV (no key, delayed).

    Uses the daily CSV ``https://stooq.com/q/d/l/?s=<sym>&i=d`` (ascending).
    Only ``1d`` is supported; other intervals raise so the chain skips them.
    Raises on network/empty/unusable data; callers convert to per-symbol
    errors or try the next chain link. Never fabricates bars.
    """
    symbol = (provider_symbol or "").strip().upper()
    if not symbol:
        raise ValueError("empty symbol")
    if (interval or "1d").strip().lower() != "1d":
        raise ValueError(f"stooq fallback supports 1d only, got {interval!r}")
    try:
        from backend.market_data.providers.stooq import to_stooq_symbol
    except Exception as exc:
        raise RuntimeError("stooq provider unavailable") from exc
    try:
        stooq_sym = to_stooq_symbol(symbol)
    except Exception as exc:
        raise RuntimeError(f"stooq symbol map failed for {symbol}") from exc
    # Cap rows: stooq daily CSV returns full history; keep the trailing 2y
    # (~504 trading days) so the fallback mirrors FETCH_PERIOD="2y".
    import csv
    import io
    import urllib.request

    url = f"https://stooq.com/q/d/l/?s={stooq_sym}&i=d"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "onemarket/1.0"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        raise RuntimeError(f"stooq fetch failed for {symbol}: {type(exc).__name__}") from exc
    try:
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
    except Exception as exc:
        raise RuntimeError(f"stooq parse failed for {symbol}") from exc
    bars: list[dict] = []
    for row in rows[-600:]:
        try:
            day = str(row.get("Date") or row.get("date") or "").strip()[:10]
            if not day or len(day) != 10:
                continue
            ts = _index_to_utc(day, symbol_hint=symbol)
            if ts is None:
                continue
            close = _fnum(row.get("Close") or row.get("close"))
            if close is None:
                continue
            bars.append({
                "ts": ts,
                "open": _fnum(row.get("Open") or row.get("open")),
                "high": _fnum(row.get("High") or row.get("high")),
                "low": _fnum(row.get("Low") or row.get("low")),
                "close": close,
                "volume": _inum(row.get("Volume") or row.get("volume")),
            })
        except Exception:
            continue
    # Drop incomplete OHLC rows (mirrors snapshot_store canonical rule).
    bars = [b for b in bars if b.get("open") is not None and b.get("high") is not None
            and b.get("low") is not None and b.get("close") is not None]
    bars.sort(key=lambda item: item["ts"])
    if not bars:
        raise RuntimeError(f"no usable stooq bars for {symbol}")
    return bars


def fetch_alpaca_daily_bars(
    provider_symbol: str,
    *,
    feed: str | None = None,
    limit: int = 5000,
) -> list[dict]:
    """Fetch split/dividend-adjusted daily bars via Alpaca (US only, needs keys).

    ``GET /v2/stocks/{SYM}/bars?timeframe=1Day&adjustment=all&sort=asc``
    (``adjustment=all`` matches the yfinance ``auto_adjust=True`` lineage
    the forecasting engine assumes). One page holds 2y of dailies
    (``limit=5000``); ``next_page_token`` is followed defensively (bounded).
    Alpaca daily timestamps are midnight ET in UTC, so the calendar date is
    the trading day itself (same convention as the yfinance path's
    exchange-midnight-in-UTC). Returns ascending
    ``[{ts (aware UTC), open, high, low, close, volume}]``. Raises on
    missing keys / non-US symbol (fast, no network) / empty history;
    callers convert that into a chain miss (never a batch 500).
    """
    symbol = (provider_symbol or "").strip().upper()
    if not symbol:
        raise ValueError("empty symbol")
    try:
        from backend.market_data.providers.alpaca import (
            BASE_URL,
            resolve_feed,
            resolve_keys,
            to_alpaca_symbol,
        )
    except Exception as exc:
        raise RuntimeError("alpaca provider unavailable") from exc
    # Fast-fail before any network: non-US symbols and missing keys.
    alpaca_symbol = to_alpaca_symbol(symbol)  # raises for .SS/.PA/.AS/.BR
    key_id, secret = resolve_keys()
    if not (key_id and secret):
        raise RuntimeError("alpaca API keys missing")
    try:
        import httpx
    except Exception as exc:
        raise RuntimeError("httpx package unavailable") from exc
    try:
        page_limit = max(100, min(int(limit), 10000))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        page_limit = 5000
    use_feed = resolve_feed(feed)
    headers = {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret}
    params: dict[str, object] = {
        "timeframe": "1Day",
        "limit": page_limit,
        "adjustment": "all",
        "sort": "asc",
        "feed": use_feed,
    }
    bars: list[dict] = []
    for _page in range(2):  # bounded: 2 x 5000 rows max (2y of dailies fits one page)
        try:
            resp = httpx.get(
                f"{BASE_URL}/v2/stocks/{alpaca_symbol}/bars",
                params=dict(params),
                headers=headers,
                timeout=12.0,
            )
        except Exception as exc:
            raise RuntimeError(f"alpaca bars failed for {symbol}: {type(exc).__name__}") from exc
        try:
            status = int(getattr(resp, "status_code", 200) or 200)
        except (TypeError, ValueError):
            status = 200
        if status in (401, 403):
            raise RuntimeError("alpaca unauthorized (check Alpaca keys)")
        if status == 404:
            raise RuntimeError(f"no data for {symbol}")
        if status == 429:
            raise RuntimeError("alpaca rate limited (429)")
        if status >= 400:
            raise RuntimeError(f"alpaca HTTP {status}")
        try:
            payload = resp.json()
        except Exception as exc:
            raise RuntimeError(f"alpaca bad JSON for {symbol}: {type(exc).__name__}") from exc
        rows = (payload or {}).get("bars") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise RuntimeError(f"alpaca unexpected bars schema for {symbol}")
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                ts = _index_to_utc(row.get("t"), symbol_hint=symbol)
            except Exception:
                continue
            if ts is None:
                continue
            close = _fnum(row.get("c"))
            if close is None:
                continue
            bars.append({
                "ts": ts,
                "open": _fnum(row.get("o")),
                "high": _fnum(row.get("h")),
                "low": _fnum(row.get("l")),
                "close": close,
                "volume": _inum(row.get("v")),
            })
        try:
            token = (payload or {}).get("next_page_token")
        except Exception:
            token = None
        if not token:
            break
        params["page_token"] = str(token)
    bars.sort(key=lambda item: item["ts"])
    if not bars:
        raise RuntimeError(f"no usable alpaca bars for {symbol}")
    return bars


#: Yahoo suffixes outside Alpaca's US-only feed (mirrors
#: providers.alpaca._NON_US_SUFFIXES; MIC unknown at this layer, so bare
#: tickers always attempt Alpaca — US default, fast-fail otherwise).
_NON_US_BAR_SUFFIXES = (".SS", ".PA", ".AS", ".BR", ".CN", ".FR", ".NL", ".BE", ".BO", ".L")


def _is_alpaca_bars_eligible(provider_symbol: str) -> bool:
    """US-only gate for the ``alpaca`` chain link (no network, never raises)."""
    try:
        upper = (provider_symbol or "").strip().upper()
    except Exception:
        return False
    return bool(upper) and not upper.endswith(_NON_US_BAR_SUFFIXES)


def fetch_daily_bars_with_fallback(
    provider_symbol: str,
    period: str = FETCH_PERIOD,
    interval: str = FETCH_INTERVAL,
    chain: list[str] | tuple[str, ...] | None = None,
) -> tuple[list[dict], str]:
    """Fetch daily bars via the ordered ``chain``; return (bars, source).

    Default chain is :func:`bar_chain` (env ``INGEST_BAR_CHAIN``). Tries each
    link in order, returning the first non-empty success. Raises the last
    error when every link fails so callers report a per-symbol error.
    ``source`` is the winning link name (``alpaca``/``yfinance``)
    for ``price_bars.source`` lineage. The ``alpaca`` link is US-only:
    non-US symbols skip it silently (routing, not failure — no warning).
    Legacy ``"stooq"`` links are skipped (provider dropped).
    """
    links = list(chain) if chain else bar_chain()
    last_exc: Exception | None = None
    for link in links:
        try:
            if link == "stooq":
                continue  # dropped provider: skip silently, never fetch
            if link == "alpaca":
                if not _is_alpaca_bars_eligible(provider_symbol):
                    continue  # US-only feed: skip silently, never warn
                return fetch_alpaca_daily_bars(provider_symbol), "alpaca"
            return fetch_daily_bars(provider_symbol, period, interval), "yfinance"
        except Exception as exc:
            last_exc = exc
            logger.warning("ingest chain link failed link=%s symbol=%s", link, provider_symbol)
            continue
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"no data for {(provider_symbol or '').strip().upper()}")


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
    """Map a registry instrument to its DB row (create on first ingest).

    Race-safe: concurrent fan-outs (screener/markets/ingest) can all miss
    the SELECT and INSERT the same (mic, symbol) — the losers hit the
    ``uq_instruments_mic_symbol`` UNIQUE constraint. On any flush failure
    roll back and re-SELECT the winner's row instead of raising (which
    previously produced 23505 errors plus ShareLock pile-ups on Postgres).
    """
    from backend.db.models import Instrument as DBInstrument

    def _find():
        return (
            db.query(DBInstrument)
            .filter(
                DBInstrument.exchange_mic == registry_instrument.exchange_mic,
                DBInstrument.exchange_symbol == registry_instrument.exchange_symbol,
            )
            .first()
        )

    row = _find()
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
    try:
        db.flush()  # assign the UUID PK before bar upserts
    except Exception:
        # Lost the create race (or any flush failure): roll back to clear
        # the failed state, then return the winner's row.
        try:
            db.rollback()
        except Exception:
            pass
        existing = _find()
        if existing is not None:
            return existing
        raise
    return row


def _upsert_bars(db, db_instrument, bars: list[dict], *, timeframe: str, source: str = "yfinance") -> int:
    """Idempotent upsert keyed (instrument_id, timeframe, ts). Returns count."""
    from backend.db.models import PriceBar

    now = _utcnow()
    grade = _grade_for_ingest()
    try:
        src = str(source or "yfinance").strip().lower() or "yfinance"
    except Exception:
        src = "yfinance"
    if src not in ("yfinance", "alpaca", "finnhub", "twelvedata"):
        src = "yfinance"
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
            source=src,
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
    fetch = fetch_fn if fetch_fn is not None else fetch_daily_bars_with_fallback

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
    # Parallel fetch, sequential persist: Yahoo fetches are I/O-bound and
    # dominate wall time (9 symbols x 5-15s sequential = 45-135s > Vercel
    # maxDuration 60). Fetch in a 4-worker pool with a 15s per-symbol
    # join, then upsert on this thread's single DB session (sessions are
    # not thread-safe). DB writes stay sequential and race-safe.
    try:
        from concurrent.futures import ThreadPoolExecutor as _TPE, as_completed as _done
        from backend.security.validation import sanitize_error as _sanerr

        def _resolve(raw_sym: str):
            try:
                instrument, _, _ = registry.resolve(raw_sym)
            except Exception:
                return None, f"unknown instrument for {raw_sym!r}"
            if instrument is not None:
                return instrument, None
            try:
                from backend.security.validation import validate_symbol as _vsym
                _vsym(raw_sym, field="symbol")
            except Exception:
                return None, f"unknown instrument for {raw_sym!r}"
            try:
                from backend.market_data.service import _provisional_instrument as _prov
            except Exception:
                _prov = None  # type: ignore[assignment]
            try:
                instrument = _prov(raw_sym) if _prov is not None else None
            except Exception:
                instrument = None
            if instrument is None:
                return None, f"unknown instrument for {raw_sym!r}"
            return instrument, None

        # Phase 1: resolve (no I/O) + submit fetches in parallel.
        resolved: dict[str, object] = {}
        for raw in wanted:
            inst, err = _resolve(raw)
            if err is not None:
                errors[raw] = err
                continue
            resolved[raw] = inst  # type: ignore[assignment]

        fetched: dict[str, tuple] = {}
        if resolved:
            def _do_fetch(item_raw: str):
                inst = resolved[item_raw]
                try:
                    provider_symbol = (
                        getattr(inst, "provider_symbol", None) or item_raw.strip().upper()
                    )
                except Exception:
                    provider_symbol = item_raw.strip().upper()
                try:
                    out = fetch(provider_symbol)
                    if isinstance(out, tuple) and len(out) == 2:
                        bars, bar_source = out[0], out[1]
                    else:
                        bars, bar_source = out, "yfinance"
                    return (item_raw, provider_symbol, bars, bar_source, None)
                except Exception as exc:
                    try:
                        reason = _sanerr(exc)[:200]
                    except Exception:
                        reason = f"{type(exc).__name__}"
                    return (item_raw, provider_symbol, None, None, reason)

            workers = max(1, min(4, len(resolved)))
            try:
                with _TPE(max_workers=workers) as _pool:
                    _futs = { _pool.submit(_do_fetch, r): r for r in resolved }
                    for _fut in _done(_futs, timeout=55):
                        try:
                            item_raw, provider_symbol, bars, bar_source, reason = _fut.result(timeout=2)
                        except Exception as exc:
                            r0 = _futs[_fut]
                            try:
                                reason0 = _sanerr(exc)[:200]
                            except Exception:
                                reason0 = "fetch timed out"
                            errors[r0] = reason0
                            continue
                        if reason is not None:
                            errors[item_raw] = reason
                            continue
                        fetched[item_raw] = (provider_symbol, bars, bar_source)
            except Exception:
                # Pool-level timeout/failure: fall back to sequential fetch
                # for any unresolved symbols so the batch still progresses.
                for r in resolved:
                    if r in fetched or r in errors:
                        continue
                    try:
                        _, provider_symbol, bars, bar_source, reason = _do_fetch(r)
                        if reason is not None:
                            errors[r] = reason
                        else:
                            fetched[r] = (provider_symbol, bars, bar_source)
                    except Exception as exc:
                        try:
                            errors[r] = _sanerr(exc)[:200]
                        except Exception:
                            errors[r] = "fetch failed"

        # Phase 2: persist sequentially on this thread's session.
        for raw in wanted:
            if raw in errors or raw not in fetched:
                continue
            try:
                provider_symbol, bars, bar_source = fetched[raw]
                instrument = resolved[raw]
                db_inst = _get_or_create_db_instrument(db, instrument)
                count = _upsert_bars(db, db_inst, bars, timeframe=timeframe, source=bar_source)
                db.commit()
                # Snapshot-on-fetch: persist a compressed snapshot of exactly
                # what this call sourced (all providers; winning link
                # attributed). Best-effort after bars are durable — never
                # breaks ingestion.
                try:
                    from backend.market_data import snapshot_store as _snapshots

                    _snapshots.save_snapshot(
                        db,
                        symbol=provider_symbol,
                        timeframe=timeframe,
                        bars=bars,
                        source=str(bar_source or "yfinance"),
                        provenance={"source": str(bar_source or "yfinance"),
                                    "fetch": "scheduled-ingest"},
                        instrument_id=getattr(db_inst, "instrument_id", None),
                        exchange_mic=getattr(instrument, "exchange_mic", None),
                    )
                except Exception:
                    pass
                ingested[provider_symbol] = count
            except Exception as exc:
                try:
                    db.rollback()
                except Exception:
                    pass
                try:
                    from backend.security.validation import sanitize_error as _sanerr2
                    errors[raw] = _sanerr2(exc)[:200]
                except Exception:
                    errors[raw] = f"{type(exc).__name__}"
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
