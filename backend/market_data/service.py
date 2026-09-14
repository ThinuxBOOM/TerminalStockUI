"""Quote service: registry -> provider -> normalize -> quality -> provenance.

Single place where the provenance envelope is attached to market data.
Deterministic stub bars (seeded by symbol) keep charts working offline.
"""

from __future__ import annotations

import hashlib
import random
from datetime import datetime, timedelta, timezone

from .health import ProviderHealthTracker, market_state
from .provenance import Provenance, build_provenance
from .quality import grade_quality
from ..instruments.calendars import expected_delay_minutes
from ..instruments.registry import InstrumentRegistry
from .providers.yfinance import YFinanceProvider

try:  # canonical absolute import; fallback to relative for alt layouts
    from backend.market_data.providers.akshare import AKShareProvider
except ImportError:  # pragma: no cover
    try:
        from .providers.akshare import AKShareProvider  # type: ignore[no-redef]
    except ImportError:
        AKShareProvider = None  # type: ignore[assignment]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_yahoo_sse_symbol(provider_symbol: str) -> str:
    """Ensure Yahoo-style ``.SS`` form for SSE (``600519`` -> ``600519.SS``)."""
    text = (provider_symbol or "").strip().upper()
    if not text:
        return text
    if text.endswith(".SS"):
        return text
    if text.isdigit() and len(text) == 6:
        return f"{text}.SS"
    return text


def _to_akshare_code(provider_symbol: str) -> str:
    """Strip ``.SS`` to the 6-digit AKShare code (``600519.SS`` -> ``600519``)."""
    text = (provider_symbol or "").strip().upper()
    if text.endswith(".SS"):
        text = text[: -len(".SS")]
    return text.strip()


def _is_sse_request(mic: str | None, provider_symbol: str, market: str | None) -> bool:
    """True when this quote should use the SSE fallback chain."""
    if (mic or "").upper() == "XSHG":
        return True
    if (market or "").strip().upper() == "XSHG":
        return True
    upper = (provider_symbol or "").strip().upper()
    if upper.endswith(".SS"):
        return True
    core = upper[:-3] if upper.endswith(".SS") else upper
    if core.isdigit() and len(core) == 6:
        return True
    return False


def _quote_is_live(quote: dict | None) -> bool:
    return bool(
        quote is not None
        and not bool(quote.get("fallback_used", False))
        and quote.get("price") is not None
    )


_GRADE_ORDER = {"A": 0, "B": 1, "C": 2, "D": 3, "F": 4, "U": 5}


def _worse_grade(first: str | None, second: str | None) -> str:
    """Worse of two quality grades (unknown grades lose)."""
    order = _GRADE_ORDER.get((first or "U").upper(), 5)
    other = _GRADE_ORDER.get((second or "U").upper(), 5)
    for grade, rank in _GRADE_ORDER.items():
        if rank == max(order, other):
            return grade
    return "U"


def _provisional_instrument(symbol_text: str):
    """Minimal registry-style instrument for symbols outside the seed list.

    Lets on-demand backfill persist bars for real symbols the curated
    registry never seeded (e.g. GOOGL): bare tickers default to XNAS (same
    default as quotes), suffixed symbols take their market from the suffix.
    Currency follows the market (XSHG->CNY, Euronext->EUR, else USD).
    Never raises; returns None when the text is unusable.
    """
    try:
        from backend.instruments.calendars import (
            provider_symbol_for,
            split_provider_symbol,
        )
        from backend.instruments.models import Instrument as RegistryInstrument
    except Exception:
        return None
    try:
        upper = str(symbol_text or "").strip().upper()
    except Exception:
        return None
    if not upper:
        return None
    try:
        base, mic_hint = split_provider_symbol(symbol_text)
    except Exception:
        base, mic_hint = upper, None
    if mic_hint:
        mic, exch = mic_hint, (base or upper)
    else:
        mic, exch = "XNAS", upper
    currency = {"XSHG": "CNY", "XPAR": "EUR", "XAMS": "EUR", "XBRU": "EUR"}.get(mic, "USD")
    try:
        provider_symbol = provider_symbol_for(exch, mic) or upper
    except Exception:
        provider_symbol = upper
    try:
        return RegistryInstrument(
            instrument_id=f"{mic}-{exch}",
            exchange_mic=mic,
            exchange_symbol=exch,
            provider_symbol=provider_symbol,
            company_name=exch,
            currency=currency,
            trading_calendar=mic,
        )
    except Exception:
        return None


class MarketDataService:
    def __init__(
        self,
        *,
        registry: InstrumentRegistry | None = None,
        provider: YFinanceProvider | None = None,
        health: ProviderHealthTracker | None = None,
        cache: object | None = None,
        akshare_provider: object | None = None,
    ) -> None:
        self.registry = registry or InstrumentRegistry()
        self.health = health or ProviderHealthTracker()
        self.provider = provider or YFinanceProvider(
            on_call=lambda p, ms, ok: self.health.record(p, ms, ok)
        )
        if getattr(self.provider, "_on_call", None) is None:
            self.provider._on_call = lambda p, ms, ok: self.health.record(p, ms, ok)
        # SSE secondary (AKShare). Independent breaker/limiter -> failure isolation.
        # Default inherits stub_mode from primary so offline/test services stay offline.
        if akshare_provider is not None:
            self.akshare_provider = akshare_provider
        elif AKShareProvider is not None:
            primary_stub = bool(getattr(self.provider, "stub_mode", False))
            try:
                self.akshare_provider = AKShareProvider(
                    on_call=lambda p, ms, ok: self.health.record(p, ms, ok),
                    stub_mode=primary_stub,
                )
            except Exception:
                self.akshare_provider = None
        else:
            self.akshare_provider = None
        if self.akshare_provider is not None and getattr(
            self.akshare_provider, "_on_call", None
        ) is None:
            try:
                self.akshare_provider._on_call = (  # type: ignore[union-attr]
                    lambda p, ms, ok: self.health.record(p, ms, ok)
                )
            except Exception:
                pass
        self.cache = cache

    def _call_akshare(self, ak_code: str) -> dict | None:
        """Invoke AKShare secondary; None when unavailable. Never raises."""
        prov = self.akshare_provider
        if prov is None:
            return None
        get_quote = getattr(prov, "get_quote", None)
        if get_quote is None:
            return None
        try:
            try:
                return get_quote(ak_code, market="XSHG")
            except TypeError:
                return get_quote(ak_code)
        except Exception as exc:
            # Preserve empty-symbol contract; otherwise treat as chain miss.
            from .providers.base import ProviderError

            if isinstance(exc, ProviderError) and "empty symbol" in str(exc).lower():
                raise
            return None

    # -- quotes ---------------------------------------------------------
    def get_quote(self, symbol: str, market: str | None = None) -> dict:
        # Harden: coerce non-str/None symbols to str (avoids AttributeError
        # on symbol.strip()); empty still flows to ProviderError via provider.
        try:
            symbol_text = str(symbol or "").strip()
        except Exception:
            symbol_text = ""
        instrument, candidates, ambiguous = self.registry.resolve(symbol_text, market)
        provider_symbol = instrument.provider_symbol if instrument else symbol_text.upper()
        try:
            mic = instrument.exchange_mic if instrument else (
                str(market).strip().upper() if market and str(market).strip() else "XNAS"
            )
        except Exception:
            mic = "XNAS"

        sse = _is_sse_request(mic, provider_symbol, market)
        if sse:
            # Canonical SSE forms: yfinance wants .SS, akshare wants 6-digit.
            yahoo_symbol = _to_yahoo_sse_symbol(provider_symbol)
            if not yahoo_symbol:
                # Preserve empty-symbol contract (ProviderError, no fallback).
                quote_empty = self.provider.get_quote(provider_symbol)
                # Above raises for empty; unreachable fallback:
                quote = quote_empty
                provider_symbol = yahoo_symbol or provider_symbol
            else:
                provider_symbol = yahoo_symbol
        cache_key = f"quote:{provider_symbol}:{mic}"
        if self.cache is not None:
            try:
                hit = self.cache.get(cache_key)  # type: ignore[union-attr]
                if hit:
                    return hit
            except Exception:
                pass

        if sse:
            # SSE fallback chain: yfinance(.SS) -> akshare(6-digit) -> stub.
            # Each provider has an independent breaker (isolation); the first
            # live (non-fallback) quote wins and sets provenance source.
            yahoo_symbol = _to_yahoo_sse_symbol(provider_symbol)
            ak_code = _to_akshare_code(yahoo_symbol)
            quote: dict | None = None
            yf_fallback: dict | None = None
            try:
                q_yf = self.provider.get_quote(yahoo_symbol)
            except Exception as exc:
                from .providers.base import ProviderError as _PE

                if isinstance(exc, _PE) and "empty symbol" in str(exc).lower():
                    raise
                q_yf = None
            if _quote_is_live(q_yf):
                quote = q_yf
            else:
                yf_fallback = q_yf
                q_ak = self._call_akshare(ak_code)
                if _quote_is_live(q_ak):
                    quote = q_ak
                elif q_ak is not None:
                    quote = q_ak
                elif yf_fallback is not None:
                    quote = yf_fallback
                else:
                    # Both providers unavailable: deterministic flagged stub (CNY).
                    quote = {
                        "symbol": yahoo_symbol,
                        "price": 100.0,
                        "currency": "CNY",
                        "as_of": _utcnow(),
                        "source": getattr(self.akshare_provider, "name", "akshare")
                        if self.akshare_provider is not None
                        else self.provider.name,
                        "missing_fields": ["open", "high", "low", "prev_close", "volume"],
                        "delay_minutes": 15,
                        "fallback_used": True,
                    }
            assert quote is not None
            # Currency must be CNY for XSHG (never USD), even on yfinance path.
            quote["currency"] = "CNY"
            if "currency" in (quote.get("missing_fields") or []):
                quote["missing_fields"] = [
                    f for f in quote["missing_fields"] if f != "currency"
                ]
            # Canonical SSE symbol for the response envelope.
            quote["symbol"] = yahoo_symbol
        else:
            try:
                quote = self.provider.get_quote(provider_symbol)
            except Exception as exc:
                from .providers.base import ProviderError as _PE

                if isinstance(exc, _PE) and "empty symbol" in str(exc).lower():
                    raise
                # Unexpected provider failure (the stock provider normally
                # degrades to a stub instead of raising): fall through to the
                # snapshot/stub outage path below. Never raises.
                quote = None
            if quote is None:
                quote = self._read_quote_snapshot(provider_symbol) or {
                    "symbol": provider_symbol,
                    "price": 100.0,
                    "currency": "USD",
                    "as_of": _utcnow(),
                    "source": self.provider.name,
                    "missing_fields": ["open", "high", "low", "prev_close", "volume"],
                    "delay_minutes": 15,
                    "fallback_used": True,
                }
        as_of = quote.get("as_of") or _utcnow()
        if not isinstance(as_of, datetime):
            as_of = _utcnow()
        elif as_of.tzinfo is None:
            # Naive provider timestamps are assumed UTC (avoids
            # naive/aware subtraction crashes in the age math below).
            as_of = as_of.replace(tzinfo=timezone.utc)
        if instrument is not None:
            mic = instrument.exchange_mic
        elif market and market.strip():
            mic = market.strip().upper()
        elif sse:
            mic = "XSHG"
        else:
            mic = "XNAS"
        try:
            expected = expected_delay_minutes(mic)
        except ValueError:
            expected = 15
        raw_age_min = (_utcnow() - as_of).total_seconds() / 60
        if raw_age_min < -5:
            # Future-dated data (beyond clock-skew tolerance): never badge as
            # fresh — surface it as unusable instead of laundering it live.
            future_dated = True
            age_min = 0.0
        else:
            future_dated = False
            age_min = max(0.0, raw_age_min)
        fallback = bool(quote.pop("fallback_used", False)) or future_dated
        if future_dated:
            quote["missing_fields"] = sorted(
                set(quote.get("missing_fields", [])) | {"as_of"}
            )
        source = quote.pop("source", self.provider.name)
        if fallback and not future_dated:
            # Outage path: prefer the last LIVE quote over a placeholder.
            # Grade/age below are recomputed from the stored as_of, so the
            # badge shows honest staleness instead of a fresh-looking stub.
            stored = self._read_quote_snapshot(provider_symbol)
            if stored is not None:
                quote = stored
                fallback = True
                source = quote.get("source", source)
                as_of = quote.get("as_of") or _utcnow()
                if not isinstance(as_of, datetime):
                    as_of = _utcnow()
                elif as_of.tzinfo is None:
                    as_of = as_of.replace(tzinfo=timezone.utc)
                age_min = max(0.0, (_utcnow() - as_of).total_seconds() / 60)
        # NOTE: live-quote write-through happens below (after grading) so the
        # snapshot stores the true live grade. Stub/fallback data is never
        # persisted (it would poison the well).
        grade, _reasons = grade_quality(
            delay_minutes=expected,
            age_minutes=age_min,
            missing_fields=quote.get("missing_fields", []),
            fallback_used=fallback,
            reconciled=False,  # single source in v1
            invalid=future_dated,
        )
        snapshot_grade = quote.pop("_snapshot_grade", None) if fallback else None
        if snapshot_grade:
            # Never grade stored data better than it was at fetch time.
            grade = _worse_grade(snapshot_grade, grade)
        if not fallback:
            self._persist_quote_snapshot(
                provider_symbol, instrument, mic, quote, source, as_of, grade,
            )
        provenance = build_provenance(
            source,
            as_of=as_of,
            delay_minutes=expected,
            quality_grade=grade,
            fallback_used=fallback,
            missing_fields=quote.get("missing_fields", []),
        )
        try:
            # Wall-clock `now`: staleness and the exchange calendar must be
            # evaluated at the present moment, never at the data timestamp
            # (now=as_of would pin age to 0 and badge stale data MARKET OPEN).
            _market_state = market_state(
                as_of, delay_minutes=expected, mic=mic, now=_utcnow()
            )
        except Exception:
            _market_state = market_state(as_of, delay_minutes=expected)
        response = {
            "symbol": provider_symbol,
            "instrument": instrument.model_dump_canonical() if instrument else None,
            "candidates": [c.exchange_symbol for c in candidates[1:]] if ambiguous else [],
            "ambiguous": ambiguous,
            "price": quote.get("price"),
            "open": quote.get("open"),
            "high": quote.get("high"),
            "low": quote.get("low"),
            "prev_close": quote.get("prev_close"),
            "volume": quote.get("volume"),
            "currency": quote.get("currency")
            or (instrument.currency if instrument else ("CNY" if sse else "USD")),
            "change": quote.get("change"),
            "change_pct": quote.get("change_pct"),
            "market_state": _market_state,
            "provenance": provenance.model_dump(mode="json"),
        }
        if self.cache is not None and not fallback:
            try:
                self.cache.set(cache_key, response, ttl_s=60)  # type: ignore[union-attr]
            except Exception:
                pass
        return response

    def provenance_for(self, payload: dict) -> Provenance:
        return Provenance(**payload["provenance"])

    # -- bars (DB-first, deterministic offline-capable stub fallback) ---
    def _bars_cache_key(self, symbol: str, timeframe: str, limit: int) -> str:
        try:
            sym = str(symbol or "").strip().upper()
        except Exception:
            sym = str(symbol)
        try:
            n = max(1, min(int(limit), 250))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            n = 30
        return f"bars:{sym}:{(timeframe or '1d')}:{n}"

    def get_bars(self, symbol: str, timeframe: str = "1d", limit: int = 30) -> dict:
        """Serve daily bars from ``price_bars`` when coverage is sufficient.

        DB path: resolve via the registry, read the latest ``limit`` rows
        for ``(instrument_id, timeframe)`` ascending; trusted when at least
        ``min(limit, 100)`` rows come back (quality gate so thin histories
        never masquerade as full coverage). Provenance then carries the
        stored source, ``fallback_used=False`` and a ``grade_quality`` grade.
        ANY exception, unknown instrument, thin/empty coverage, or an
        unreachable DB falls back to the deterministic stub below, so
        offline/test environments never break.

        Before the stub, an on-demand live fetch is attempted (``1d`` only):
        the first chart view of a never-ingested symbol persists real bars,
        so later views are DB-served real data. The stub is anchored to the
        current quote (best-effort) so its last close matches the header
        price — an unanchored random base would render a chart on a
        completely different scale than the quote.

        Results are cached 120s (bars move slowly; quote anchor is already
        quote-cached) so screener/forecast/backtest fan-outs sharing a
        symbol pay one DB/stub build per two minutes, not one per horizon.
        """
        cache_key: str | None = None
        if self.cache is not None:
            try:
                cache_key = self._bars_cache_key(symbol, timeframe, limit)
                hit = self.cache.get(cache_key)  # type: ignore[union-attr]
                if isinstance(hit, dict) and isinstance(hit.get("bars"), list):
                    return hit
            except Exception:
                cache_key = None
        try:
            db_out = self._get_bars_from_db(symbol, timeframe, limit)
            if db_out is not None:
                if self.cache is not None and cache_key is not None:
                    try:
                        self.cache.set(cache_key, db_out, ttl_s=120)  # type: ignore[union-attr]
                    except Exception:
                        pass
                return db_out
        except Exception:
            pass
        try:
            if self._fetch_and_store_bars(symbol, timeframe):
                db_out = self._get_bars_from_db(symbol, timeframe, limit)
                if db_out is not None:
                    if self.cache is not None and cache_key is not None:
                        try:
                            self.cache.set(cache_key, db_out, ttl_s=120)  # type: ignore[union-attr]
                        except Exception:
                            pass
                    return db_out
        except Exception:
            pass
        out = self._stub_bars(symbol, timeframe, limit, anchor=self._quote_anchor(symbol))
        if self.cache is not None and cache_key is not None:
            try:
                self.cache.set(cache_key, out, ttl_s=120)  # type: ignore[union-attr]
            except Exception:
                pass
        return out

    def get_bars_many(
        self, symbols: list[str], timeframe: str = "1d", limit: int = 30
    ) -> dict[str, dict]:
        """Bulk bars for a symbol list (one worker pool, shared cache).

        Never raises: per-symbol failures are skipped from the dict.
        Used by screener/markets fan-outs to avoid N sequential round trips
        blocking one request thread.
        """
        from concurrent.futures import ThreadPoolExecutor

        wanted: list[str] = []
        for raw in symbols or []:
            try:
                text = str(raw or "").strip()
            except Exception:
                continue
            if text:
                wanted.append(text)
        if not wanted:
            return {}
        out: dict[str, dict] = {}

        def _one(sym: str) -> tuple[str, dict | None]:
            try:
                return sym, self.get_bars(sym, timeframe=timeframe, limit=limit)
            except Exception:
                return sym, None

        workers = max(1, min(8, len(wanted)))
        try:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for sym, payload in pool.map(_one, wanted):
                    if isinstance(payload, dict):
                        out[sym] = payload
        except Exception:
            for sym in wanted:
                try:
                    payload = self.get_bars(sym, timeframe=timeframe, limit=limit)
                except Exception:
                    continue
                if isinstance(payload, dict):
                    out[sym] = payload
        return out

    def get_quotes_many(
        self, symbols: list[str], market: str | None = None
    ) -> dict[str, dict]:
        """Bulk quotes for a symbol list (bounded pool, per-symbol degrade).

        Never raises; failures are skipped. Homepage/screener fan-outs use
        this instead of N sequential ``get_quote`` calls.
        """
        from concurrent.futures import ThreadPoolExecutor

        wanted: list[str] = []
        for raw in symbols or []:
            try:
                text = str(raw or "").strip()
            except Exception:
                continue
            if text:
                wanted.append(text)
        if not wanted:
            return {}
        out: dict[str, dict] = {}

        def _one(sym: str) -> tuple[str, dict | None]:
            try:
                return sym, self.get_quote(sym, market)
            except Exception:
                return sym, None

        workers = max(1, min(8, len(wanted)))
        try:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for sym, payload in pool.map(_one, wanted):
                    if isinstance(payload, dict):
                        out[sym] = payload
        except Exception:
            for sym in wanted:
                try:
                    payload = self.get_quote(sym, market)
                except Exception:
                    continue
                if isinstance(payload, dict):
                    out[sym] = payload
        return out

    def _quote_anchor(self, symbol: str) -> float | None:
        """Best-effort reference price for anchoring stub bars.

        Returns the current quote price (live or stub — either way it is the
        same number the header shows) or None when no usable price exists.
        Never raises: the bars fallback must survive quote failures.
        """
        try:
            quote = self.get_quote(symbol)
        except Exception:
            return None
        if not isinstance(quote, dict):
            return None
        try:
            price = float(quote.get("price"))
        except (TypeError, ValueError):
            return None
        if not price or price <= 0 or price != price or price == float("inf"):
            return None
        return price

    # -- last-fetched persistence (write-through quotes, snapshot fallback) --
    @staticmethod
    def _safe_num(value) -> float | None:
        try:
            number = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        if number != number or number in (float("inf"), float("-inf")):
            return None
        return number

    def _persist_quote_snapshot(
        self,
        provider_symbol: str,
        instrument,
        mic: str,
        quote: dict,
        source: str,
        as_of,
        grade: str,
    ) -> None:
        """Upsert the last LIVE quote. Best-effort: never raises.

        Only live quotes reach here (callers never persist fallback/stub
        data). First write on a fresh DB creates tables once, then retries.
        """
        try:
            from backend.db.models import QuoteSnapshot
            from backend.db.session import get_session_factory, init_db
        except Exception:
            return
        price = self._safe_num(quote.get("price"))
        if not price or price <= 0:
            return
        try:
            kwargs = {
                "symbol": provider_symbol,
                "instrument_id": getattr(instrument, "instrument_id", None),
                "exchange_mic": mic,
                "price": price,
                "open": self._safe_num(quote.get("open")),
                "high": self._safe_num(quote.get("high")),
                "low": self._safe_num(quote.get("low")),
                "prev_close": self._safe_num(quote.get("prev_close")),
                "volume": quote.get("volume")
                if isinstance(quote.get("volume"), int) else None,
                "currency": (quote.get("currency") or "USD"),
                "change": self._safe_num(quote.get("change")),
                "change_pct": self._safe_num(quote.get("change_pct")),
                "source": source or self.provider.name,
                "as_of": as_of,
                "quality_grade": (grade or "C"),
                "updated_at": _utcnow(),
            }
        except Exception:
            return
        for attempt in range(2):
            try:
                if attempt:
                    init_db()
                Session = get_session_factory()
                db = Session()
                try:
                    db.merge(QuoteSnapshot(**kwargs))
                    db.commit()
                except Exception:
                    try:
                        db.rollback()
                    except Exception:
                        pass
                    if not attempt:
                        continue
                    return
                finally:
                    try:
                        db.close()
                    except Exception:
                        pass
                return
            except Exception:
                if not attempt:
                    continue
                return

    def _read_quote_snapshot(self, provider_symbol: str) -> dict | None:
        """Last live quote as a provider-shaped dict, or None. Never raises."""
        try:
            from backend.db.models import QuoteSnapshot
            from backend.db.session import get_session_factory
        except Exception:
            return None
        try:
            db = get_session_factory()()
        except Exception:
            return None
        try:
            row = (
                db.query(QuoteSnapshot)
                .filter(QuoteSnapshot.symbol == provider_symbol)
                .first()
            )
            if row is None or row.price is None:
                return None
            as_of = row.as_of
            if isinstance(as_of, datetime):
                if as_of.tzinfo is None:
                    as_of = as_of.replace(tzinfo=timezone.utc)
            else:
                as_of = _utcnow()

            def _col(name: str):
                try:
                    value = getattr(row, name)
                except Exception:
                    return None
                return self._safe_num(value)

            field_values = {
                "open": _col("open"),
                "high": _col("high"),
                "low": _col("low"),
                "prev_close": _col("prev_close"),
                "volume": row.volume if isinstance(row.volume, int) else None,
            }
            # Completeness is re-derived from the stored nulls (never claim
            # a full envelope for a price-only snapshot).
            missing = sorted(
                name for name, value in field_values.items() if value is None
            )
            return {
                "symbol": provider_symbol,
                "price": float(row.price),
                "open": field_values["open"],
                "high": field_values["high"],
                "low": field_values["low"],
                "prev_close": field_values["prev_close"],
                "volume": field_values["volume"],
                "currency": row.currency or "USD",
                "change": _col("change"),
                "change_pct": _col("change_pct"),
                "source": row.source or self.provider.name,
                "as_of": as_of,
                "missing_fields": missing,
                "fallback_used": True,
                "_snapshot_grade": row.quality_grade or "C",
            }
        except Exception:
            return None
        finally:
            try:
                db.close()
            except Exception:
                pass

    def _remember_fetch_miss(self, miss_key: str) -> None:
        """Negative-cache a failed on-demand bars fetch (300s). Never raises."""
        if self.cache is None:
            return
        try:
            self.cache.set(miss_key, "miss", ttl_s=300)  # type: ignore[union-attr]
        except Exception:
            pass

    def _fetch_and_store_bars(self, symbol: str, timeframe: str = "1d") -> bool:
        """Fetch live daily bars and upsert them. True when DB likely serves now.

        On-demand backfill for symbols the cron universe never ingested: the
        first chart view persists real bars, later views are DB-served.
        Only ``1d`` is fetched (storing daily bars under another timeframe
        would be dishonest). Misses are negatively cached (300s) so an
        unfetchable symbol does not pay a network timeout on every view.
        Never raises.
        """
        if (timeframe or "1d") != "1d":
            return False
        try:
            symbol_text = str(symbol or "").strip()
        except Exception:
            return False
        if not symbol_text:
            return False
        try:
            instrument, _, _ = self.registry.resolve(symbol_text)
        except Exception:
            return False
        if instrument is None:
            # Real symbol outside the curated seed list (e.g. GOOGL):
            # provision a minimal instrument so its bars can persist.
            # Junk input still fails at fetch below (miss marker, no row).
            instrument = _provisional_instrument(symbol_text)
            if instrument is None:
                return False
        provider_symbol = instrument.provider_symbol or symbol_text.upper()
        try:
            if _is_sse_request(instrument.exchange_mic, provider_symbol, None):
                provider_symbol = _to_yahoo_sse_symbol(provider_symbol)
        except Exception:
            pass
        miss_key = f"barsfetch:{provider_symbol}"
        if self.cache is not None:
            try:
                if self.cache.get(miss_key):  # type: ignore[union-attr]
                    return False
            except Exception:
                pass
        try:
            from backend.db.session import get_session_factory, init_db
            from backend.market_data.ingest import (
                _get_or_create_db_instrument,
                _upsert_bars,
                fetch_daily_bars,
            )
        except Exception:
            return False
        try:
            bars = fetch_daily_bars(provider_symbol)
        except Exception:
            self._remember_fetch_miss(miss_key)
            return False
        if not bars:
            self._remember_fetch_miss(miss_key)
            return False
        try:
            init_db()
            Session = get_session_factory()
            db = Session()
            try:
                db_inst = _get_or_create_db_instrument(db, instrument)
                _upsert_bars(db, db_inst, bars, timeframe="1d")
                db.commit()
            except Exception:
                try:
                    db.rollback()
                except Exception:
                    pass
                self._remember_fetch_miss(miss_key)
                return False
            finally:
                try:
                    db.close()
                except Exception:
                    pass
        except Exception:
            self._remember_fetch_miss(miss_key)
            return False
        if self.cache is not None:
            try:
                self.cache.delete(miss_key)  # type: ignore[union-attr]
            except Exception:
                pass
        return True

    def _get_bars_from_db(
        self, symbol: str, timeframe: str = "1d", limit: int = 30
    ) -> dict | None:
        """Read bars from price_bars; None when the DB path must not serve."""
        from backend.db.models import Instrument as DBInstrument
        from backend.db.session import get_session_factory

        try:
            symbol_text = str(symbol or "").strip()
        except Exception:
            return None
        instrument, _, _ = self.registry.resolve(symbol_text)
        try:
            n = max(1, min(int(limit), 250))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            n = 30
        Session = get_session_factory()  # lazy per call; cached engine
        db = Session()
        try:
            if instrument is not None:
                db_inst = (
                    db.query(DBInstrument)
                    .filter(
                        DBInstrument.exchange_mic == instrument.exchange_mic,
                        DBInstrument.exchange_symbol == instrument.exchange_symbol,
                    )
                    .first()
                )
                response_symbol = instrument.provider_symbol
                response_inst_id = instrument.instrument_id
            else:
                # Auto-provisioned symbols (first seen via on-demand
                # backfill): locate by provider symbol directly.
                upper = symbol_text.upper()
                db_inst = (
                    db.query(DBInstrument)
                    .filter(DBInstrument.provider_symbol == upper)
                    .first()
                )
                if db_inst is None:
                    db_inst = (
                        db.query(DBInstrument)
                        .filter(DBInstrument.exchange_symbol == upper)
                        .first()
                    )
                response_symbol = (
                    db_inst.provider_symbol if db_inst is not None
                    else symbol_text.upper()
                )
                response_inst_id = (
                    str(db_inst.instrument_id) if db_inst is not None else None
                )
            if db_inst is None:
                return None
            return self._bars_response_from_db(
                db, db_inst,
                response_symbol=response_symbol,
                response_inst_id=response_inst_id,
                timeframe=timeframe, limit_n=n,
            )
        finally:
            try:
                db.close()
            except Exception:
                pass

    @staticmethod
    def _bars_response_from_db(
        db, db_inst, *, response_symbol: str, response_inst_id,
        timeframe: str, limit_n: int,
    ) -> dict | None:
        """Build the bars payload from a DB instrument row (shared tail)."""
        from backend.db.models import PriceBar

        desc = (
            db.query(PriceBar)
            .filter(
                PriceBar.instrument_id == db_inst.instrument_id,
                PriceBar.timeframe == timeframe,
            )
            .order_by(PriceBar.ts.desc())
            .limit(limit_n)
            .all()
        )
        if len(desc) < min(limit_n, 100):
            return None
        rows = list(reversed(desc))
        bars: list[dict] = []
        sources: list[str] = []
        latest_as_of = None
        for row in rows:
            ts = row.ts
            if isinstance(ts, datetime):
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                ts_iso = ts.isoformat()
            else:
                ts_iso = str(ts)
            as_of = row.as_of
            if isinstance(as_of, datetime):
                if as_of.tzinfo is None:
                    as_of = as_of.replace(tzinfo=timezone.utc)
                if latest_as_of is None or as_of > latest_as_of:
                    latest_as_of = as_of
            if row.source:
                sources.append(str(row.source))
            bars.append({
                "ts": ts_iso,
                "open": float(row.open) if row.open is not None else None,
                "high": float(row.high) if row.high is not None else None,
                "low": float(row.low) if row.low is not None else None,
                "close": float(row.close) if row.close is not None else None,
                "volume": int(row.volume) if row.volume is not None else None,
                "missing_fields": [],
            })
        if not bars:
            return None
        # Deterministic source pick: ties broken alphabetically so same
        # inputs always yield the same provenance (set order is random).
        source = max(sorted(set(sources)), key=sources.count) if sources else "yfinance"
        mic = db_inst.exchange_mic or "XNAS"
        try:
            expected = expected_delay_minutes(mic)
        except ValueError:
            expected = 15
        as_of_stamp = latest_as_of or _utcnow()
        age_min = max(0.0, (_utcnow() - as_of_stamp).total_seconds() / 60)
        grade, _reasons = grade_quality(
            delay_minutes=expected,
            age_minutes=age_min,
            missing_fields=[],
            fallback_used=False,
            reconciled=False,  # single source in v1
        )
        provenance = build_provenance(
            source, as_of=as_of_stamp, delay_minutes=expected,
            quality_grade=grade, fallback_used=False, missing_fields=[],
        )
        return {
            "symbol": response_symbol,
            "instrument_id": response_inst_id,
            "timeframe": timeframe,
            "bars": bars,
            "provenance": provenance.model_dump(mode="json"),
        }

    def _stub_bars(
        self, symbol: str, timeframe: str = "1d", limit: int = 30,
        anchor: float | None = None,
    ) -> dict:
        try:
            symbol_text = str(symbol or "").strip()
        except Exception:
            symbol_text = ""
        instrument, _, _ = self.registry.resolve(symbol_text)
        provider_symbol = instrument.provider_symbol if instrument else symbol_text.upper()
        try:
            n = max(1, min(int(limit), 250))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            n = 30
        seed = int(hashlib.sha256(provider_symbol.encode()).hexdigest(), 16) % (2**32)
        rng = random.Random(seed)
        base = 100.0 + (seed % 900)
        price = base
        rows: list[dict] = []
        day = _utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        for i in range(n):
            drift = rng.uniform(-0.015, 0.015)
            o = price
            c = round(o * (1 + drift), 2)
            h = round(max(o, c) * (1 + rng.uniform(0, 0.008)), 2)
            low = round(min(o, c) * (1 - rng.uniform(0, 0.008)), 2)
            rows.append({
                "ts": (day - timedelta(days=(n - 1 - i))).isoformat(),
                "open": round(o, 2), "high": h, "low": low, "close": c,
                "volume": rng.randint(100_000, 60_000_000),
                "missing_fields": [],
            })
            price = c
        if anchor is not None and anchor > 0 and rows:
            # Rescale so the last close lands exactly on the quote price.
            # A constant factor preserves % returns and OHLC ordering, so
            # downstream return-based features are unaffected in relative terms.
            last_close = rows[-1].get("close") or 0
            if last_close and last_close > 0:
                factor = anchor / last_close
                for row in rows:
                    for key in ("open", "high", "low", "close"):
                        value = row.get(key)
                        if isinstance(value, (int, float)) and value > 0:
                            row[key] = round(value * factor, 2)
                rows[-1]["close"] = round(anchor, 2)
        mic = instrument.exchange_mic if instrument else "XNAS"
        try:
            expected = expected_delay_minutes(mic)
        except ValueError:
            expected = 15
        provenance = build_provenance(
            "yfinance", as_of=_utcnow(), delay_minutes=expected,
            quality_grade="C", fallback_used=True, missing_fields=[],
        )
        return {
            "symbol": provider_symbol,
            "instrument_id": instrument.instrument_id if instrument else None,
            "timeframe": timeframe,
            "bars": rows,
            "provenance": provenance.model_dump(mode="json"),
        }
