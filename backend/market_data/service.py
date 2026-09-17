"""Quote service: registry -> provider -> normalize -> quality -> provenance.

Single place where the provenance envelope is attached to market data.
Fail-closed: only live provider data is ever served. When no provider in
the chain serves a live quote (or no live bars exist), the request raises
ProviderError — routers map that to 502/503. No stubs, no snapshots-as-cover,
no stale data. Ever.
"""

from __future__ import annotations

import hashlib
import logging
import random
import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache

logger = logging.getLogger(__name__)

from .health import ProviderHealthTracker, market_state
from .provenance import Provenance, build_provenance
from .quality import grade_quality
from ..instruments.calendars import expected_delay_minutes
from ..instruments.registry import InstrumentRegistry
from .providers.yfinance import YFinanceProvider

try:  # Milestone 0 live-data extension (opt-in; None when unavailable)
    from backend.market_data.providers.alpaca import AlpacaProvider
except ImportError:  # pragma: no cover
    try:
        from .providers.alpaca import AlpacaProvider  # type: ignore[no-redef]
    except ImportError:
        AlpacaProvider = None  # type: ignore[assignment]

try:  # Free-tier real-time US redo (opt-in; None when unavailable)
    from backend.market_data.providers.finnhub_free import FinnhubProvider
except ImportError:  # pragma: no cover
    try:
        from .providers.finnhub_free import FinnhubProvider  # type: ignore[no-redef]
    except ImportError:
        FinnhubProvider = None  # type: ignore[assignment]

try:  # Free Basic-tier real-time US redo (opt-in; None when unavailable)
    from backend.market_data.providers.twelvedata_free import TwelveDataProvider
except ImportError:  # pragma: no cover
    try:
        from .providers.twelvedata_free import TwelveDataProvider  # type: ignore[no-redef]
    except ImportError:
        TwelveDataProvider = None  # type: ignore[assignment]


#: Live real-time US sources: a currently-live quote from one of these is
#: served with ``delay_minutes=0`` (grade path unchanged otherwise).
#: All three are single-venue/composite feeds (Alpaca IEX, Finnhub US,
#: TwelveData limited-venue US) — live-but-partial, never full NBBO/SIP.
_LIVE_DELAY_ZERO_SOURCES = frozenset({"alpaca", "finnhub", "twelvedata"})

#: Cooldown after a failed Alpaca bars attempt before the next one (one slow
#: view per outage, then fast yfinance-served views until the cooldown
#: lapses — never a per-view network timeout while Alpaca is down).
_ALPACA_BARS_COOLDOWN_S = 900.0


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


def _is_sse_request(mic: str | None, provider_symbol: str, market: str | None) -> bool:
    """True when this quote should use the SSE path (yfinance .SS only)."""
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

    Results are cached (LRU, 512 entries) on the normalized UPPER form — a
    fresh ``model_copy()`` is returned per call so callers can never mutate
    the cached canonical.
    """
    try:
        upper = str(symbol_text or "").strip().upper()
    except Exception:
        return None
    if not upper:
        return None
    cached = _provisional_instrument_cached(upper)
    if cached is None:
        return None
    try:
        return cached.model_copy()
    except Exception:
        return cached


@lru_cache(maxsize=512)
def _provisional_instrument_cached(upper: str):
    """Cached builder for :func:`_provisional_instrument` (UPPER input)."""
    try:
        from backend.instruments.calendars import (
            provider_symbol_for,
            split_provider_symbol,
        )
        from backend.instruments.models import Instrument as RegistryInstrument
    except Exception:
        return None
    if not upper:
        return None
    try:
        base, mic_hint = split_provider_symbol(upper)
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
        alpaca_provider: object | None = None,
        stooq_provider: object | None = None,
        finnhub_provider: object | None = None,
        twelvedata_provider: object | None = None,
    ) -> None:
        self.registry = registry or InstrumentRegistry()
        self.health = health or ProviderHealthTracker()
        self.provider = provider or YFinanceProvider(
            on_call=lambda p, ms, ok, **kw: self.health.record(p, ms, ok, status_code=kw.get("status_code"), error=kw.get("error"))
        )
        if getattr(self.provider, "_on_call", None) is None:
            self.provider._on_call = lambda p, ms, ok, **kw: self.health.record(p, ms, ok, status_code=kw.get("status_code"), error=kw.get("error"))
        # Dropped providers (akshare/stooq): constructor still accepts the
        # legacy kwargs so old call sites don't crash, but they are always
        # ignored — SSE is yfinance-only and the non-SSE chain ends at
        # twelvedata. Provider modules remain on disk, dormant.
        self.akshare_provider = None
        # Milestone 0 live-data chain (opt-in, additive — default None keeps
        # every existing call site on the yfinance behavior).
        # deps.get_market_service() wires real instances; tests pass explicit
        # stub/live doubles.
        self.alpaca_provider = alpaca_provider
        if self.alpaca_provider is not None and getattr(
            self.alpaca_provider, "_on_call", None
        ) is None:
            try:
                self.alpaca_provider._on_call = (  # type: ignore[union-attr]
                    lambda p, ms, ok, **kw: self.health.record(p, ms, ok, status_code=kw.get("status_code"), error=kw.get("error"))
                )
            except Exception:
                pass
        self.stooq_provider = None
        # Free-tier US redundancy (additive, same pattern): Finnhub +
        # TwelveData sit after yfinance in the live chain.
        # Absent (None) they are skipped, preserving pre-chain behavior.
        self.finnhub_provider = finnhub_provider
        if self.finnhub_provider is not None and getattr(
            self.finnhub_provider, "_on_call", None
        ) is None:
            try:
                self.finnhub_provider._on_call = (  # type: ignore[union-attr]
                    lambda p, ms, ok, **kw: self.health.record(p, ms, ok, status_code=kw.get("status_code"), error=kw.get("error"))
                )
            except Exception:
                pass
        self.twelvedata_provider = twelvedata_provider
        if self.twelvedata_provider is not None and getattr(
            self.twelvedata_provider, "_on_call", None
        ) is None:
            try:
                self.twelvedata_provider._on_call = (  # type: ignore[union-attr]
                    lambda p, ms, ok, **kw: self.health.record(p, ms, ok, status_code=kw.get("status_code"), error=kw.get("error"))
                )
            except Exception:
                pass
        self.cache = cache
        # Monotonic deadline until which Alpaca bars attempts are skipped
        # (set on failure; see _alpaca_bars_wanted). Per-process, GIL-atomic.
        self._alpaca_bars_unavailable_until: float = 0.0

    @staticmethod
    def _is_alpaca_eligible(mic: str | None, provider_symbol: str) -> bool:
        """Alpaca is US-only: skip SSE + Euronext before any network call."""
        try:
            upper_mic = (mic or "").strip().upper()
        except Exception:
            upper_mic = ""
        if upper_mic in ("XSHG", "XPAR", "XAMS", "XBRU"):
            return False
        upper = (provider_symbol or "").strip().upper()
        for suffix in (".SS", ".PA", ".AS", ".BR", ".CN", ".FR", ".NL", ".BE", ".BO", ".L"):
            if upper.endswith(suffix):
                return False
        return True

    @staticmethod
    def _is_finnhub_eligible(mic: str | None, provider_symbol: str) -> bool:
        """Finnhub free tier is US-only (international is EOD-only there).

        Separate method (not an alias) so future subscription tiers can
        diverge per provider without touching the Alpaca rule.
        """
        return MarketDataService._is_alpaca_eligible(mic, provider_symbol)

    @staticmethod
    def _is_twelvedata_eligible(mic: str | None, provider_symbol: str) -> bool:
        """TwelveData free Basic tier is US-only (Euronext needs Grow+).

        Separate method (not an alias) so future subscription tiers can
        diverge per provider without touching the Alpaca rule.
        """
        return MarketDataService._is_alpaca_eligible(mic, provider_symbol)

    def _alpaca_bars_wanted(self, mic: str | None, provider_symbol: str) -> bool:
        """True when an Alpaca bars attempt should be made (all local checks).

        US-eligible symbol AND resolvable keys AND no active failure
        cooldown. Never performs network I/O. Never raises.
        """
        try:
            if not MarketDataService._is_alpaca_eligible(mic, provider_symbol):
                return False
            try:
                if time.monotonic() < float(
                    getattr(self, "_alpaca_bars_unavailable_until", 0.0) or 0.0
                ):
                    return False
            except Exception:
                pass
            try:
                from backend.market_data.ingest import _alpaca_keys_present

                if not _alpaca_keys_present():
                    return False
            except Exception:
                return False
            return True
        except Exception:
            return False

    def _bars_should_prefer_alpaca_refresh(
        self, symbol: str, timeframe: str, payload: dict | None
    ) -> bool:
        """True when fresh DB bars must be refreshed from Alpaca anyway.

        US-market charts render from Alpaca (same feed as Alpaca quotes), so
        a fresh DB payload whose majority source is NOT alpaca is refreshed
        instead of served — one live Alpaca upsert flips the whole overlapping
        history (merge overwrites same-ts rows). Non-1d timeframes, non-US
        symbols, missing keys, active cooldowns, and already-Alpaca payloads
        return False (serve as today). Never raises.
        """
        try:
            if (timeframe or "1d") != "1d":
                return False
            if not isinstance(payload, dict):
                return False
            try:
                source = str(
                    (payload.get("provenance") or {}).get("source") or ""
                ).strip().lower()
            except Exception:
                source = ""
            if source == "alpaca":
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
                return False
            try:
                mic = getattr(instrument, "exchange_mic", None)
                provider_symbol = (
                    getattr(instrument, "provider_symbol", None) or symbol_text.upper()
                )
            except Exception:
                return False
            return bool(self._alpaca_bars_wanted(mic, provider_symbol))
        except Exception:
            return False

    def _call_chain_provider(self, prov: object, symbol: str, market: str | None) -> dict | None:
        """Invoke one opt-in chain provider; None on miss. Never raises.

        Preserves the empty-symbol contract (ProviderError propagates) so
        ``GET /quote?symbol=`` stays a 422, never a 200 stub.
        """
        if prov is None:
            return None
        get_quote = getattr(prov, "get_quote", None)
        if get_quote is None:
            return None
        try:
            try:
                return get_quote(symbol, market=market)
            except TypeError:
                return get_quote(symbol)
        except Exception as exc:
            from .providers.base import ProviderError

            if isinstance(exc, ProviderError) and "empty symbol" in str(exc).lower():
                raise
            return None

    # -- quotes ---------------------------------------------------------
    # Future subscription tiers (Free/Silver/Gold/Platinum): provider
    # gating will happen HERE, in one place, keyed off ``tier`` (and
    # ``user_id`` for per-user keys/quotas) — never inside providers.
    # Draft mapping (no behavior today; both params are accepted and
    # ignored): Free -> keyless only (yfinance); Silver ->
    # + keyed free tiers (Alpaca/Finnhub/TwelveData shared keys);
    # Gold/Platinum -> + paid providers (SIP, real-time Euronext, full
    # TwelveData markets) with per-user keys. Paid provider slots should
    # be added as new ``*_provider`` constructor args following the
    # existing opt-in pattern (None = skipped, chain behavior unchanged).
    def get_quote(
        self,
        symbol: str,
        market: str | None = None,
        *,
        user_id: str | None = None,
        tier: str | None = None,
    ) -> dict:
        _ = (user_id, tier)  # reserved for future per-user tier routing; no-op today.
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
            # Canonical SSE form: yfinance wants .SS.
            yahoo_symbol = _to_yahoo_sse_symbol(provider_symbol)
            if not yahoo_symbol:
                # Preserve empty-symbol contract (ProviderError, no fallback).
                quote_empty = self.provider.get_quote(provider_symbol)
                # Above raises for empty; unreachable fallback:
                quote = quote_empty
                provider_symbol = yahoo_symbol or provider_symbol
            else:
                provider_symbol = yahoo_symbol
        # Canonical cache key: upper-case symbol+MIC so aapl:XNAS and
        # AAPL:XNAS share one entry instead of double-fetching.
        try:
            _cache_sym = str(provider_symbol or "").strip().upper()
        except Exception:
            _cache_sym = str(provider_symbol)
        try:
            _cache_mic = str(mic or "").strip().upper()
        except Exception:
            _cache_mic = str(mic)
        cache_key = f"quote:{_cache_sym}:{_cache_mic}"
        if self.cache is not None:
            try:
                hit = self.cache.get(cache_key)  # type: ignore[union-attr]
                if hit:
                    return hit
            except Exception:
                pass

        if sse:
            # SSE path: yfinance(.SS) only (akshare dropped). Fail-closed:
            # when yfinance serves no live data the request raises instead
            # of serving a synthetic stub — no fallbacks, no stale
            # snapshots, no fabricated prices.
            from .providers.base import ProviderError as _PE

            yahoo_symbol = _to_yahoo_sse_symbol(provider_symbol)
            quote: dict | None = None
            try:
                q_yf = self.provider.get_quote(yahoo_symbol)
            except Exception as exc:
                if isinstance(exc, _PE) and "empty symbol" in str(exc).lower():
                    raise
                q_yf = None
            if _quote_is_live(q_yf):
                quote = q_yf
            if quote is None:
                raise _PE(
                    getattr(self.provider, "name", "yfinance"),
                    f"no live quote for {yahoo_symbol} (yfinance unavailable)",
                )
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
            # Non-SSE chain (opt-in, additive): alpaca live (US, delay 0) ->
            # yfinance (delay 15) -> finnhub live (US free, delay 0) ->
            # twelvedata live (US free, delay 0). First live quote wins
            # (alpaca preferred = freshest) with short-circuit so one live
            # feed costs one call. When nothing is live the request raises
            # (fail-closed — see below). Absent providers (None) are
            # skipped, so every existing call site without explicit wiring
            # behaves as before.
            q_alpaca: dict | None = None
            q_yf_chain: dict | None = None
            q_finnhub: dict | None = None
            q_twelvedata: dict | None = None
            quote = None
            if self._is_alpaca_eligible(mic, provider_symbol):
                # Empty-symbol contract propagates (422, never a stub).
                q_alpaca = self._call_chain_provider(
                    self.alpaca_provider, provider_symbol, mic
                )
                if _quote_is_live(q_alpaca):
                    quote = q_alpaca
            if quote is None:
                try:
                    q_yf_chain = self.provider.get_quote(provider_symbol)
                except Exception as exc:
                    from .providers.base import ProviderError as _PE

                    if isinstance(exc, _PE) and "empty symbol" in str(exc).lower():
                        raise
                    # Unexpected provider failure (the stock provider normally
                    # degrades to a stub instead of raising): fall through to the
                    # snapshot/stub outage path below. Never raises.
                    q_yf_chain = None
                if _quote_is_live(q_yf_chain):
                    quote = q_yf_chain
            if quote is None and self._is_finnhub_eligible(mic, provider_symbol):
                q_finnhub = self._call_chain_provider(
                    self.finnhub_provider, provider_symbol, mic
                )
                if _quote_is_live(q_finnhub):
                    quote = q_finnhub
            if quote is None and self._is_twelvedata_eligible(mic, provider_symbol):
                q_twelvedata = self._call_chain_provider(
                    self.twelvedata_provider, provider_symbol, mic
                )
                if _quote_is_live(q_twelvedata):
                    quote = q_twelvedata
            # Fail-closed: no fallback preference loop, no snapshot cover,
            # no synthetic stub. Either a live quote won above or the
            # request raises — routers map this to 502, never 200+stale.
            if quote is None:
                from .providers.base import ProviderError as _PE

                raise _PE(
                    getattr(self.provider, "name", "market-data"),
                    f"no live quote for {provider_symbol} (all providers unavailable)",
                )
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
            calendar_expected = expected_delay_minutes(mic)
        except ValueError:
            calendar_expected = 15
        # Live real-time US feeds (Alpaca IEX, Finnhub free, TwelveData
        # free) serve delay 0; everything else keeps the calendar delay
        # (15). Fallback/snapshot data never gets delay 0 — only a
        # currently-live quote from one of those sources does.
        try:
            _live_source = (quote.get("source") or "") if isinstance(quote, dict) else ""
        except Exception:
            _live_source = ""
        if _live_source in _LIVE_DELAY_ZERO_SOURCES and _quote_is_live(quote):
            expected = 0
        else:
            expected = calendar_expected
        raw_age_min = (_utcnow() - as_of).total_seconds() / 60
        if raw_age_min < -5:
            # Future-dated data (beyond clock-skew tolerance) is unusable:
            # fail closed instead of laundering it as live.
            from .providers.base import ProviderError as _PE

            raise _PE(
                getattr(self.provider, "name", "market-data"),
                f"future-dated quote for {provider_symbol} (as_of ahead of now)",
            )
        future_dated = False
        age_min = max(0.0, raw_age_min)
        # Fail-closed invariant: only live quotes reach this point (the chain
        # above raises otherwise). Any lingering fallback flag is a contract
        # violation, never something to serve.
        lingering = bool(quote.pop("fallback_used", False))
        if lingering or not _quote_is_live(quote):
            from .providers.base import ProviderError as _PE

            raise _PE(
                getattr(self.provider, "name", "market-data"),
                f"no live quote for {provider_symbol} (non-live data refused)",
            )
        fallback = False
        source = quote.pop("source", self.provider.name)
        # NOTE: live-quote write-through happens below (after grading) so the
        # snapshot stores the true live grade.
        grade, _reasons = grade_quality(
            delay_minutes=expected,
            age_minutes=age_min,
            missing_fields=quote.get("missing_fields", []),
            fallback_used=False,
            reconciled=False,  # single source in v1
            invalid=False,
        )
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

    # -- bars (DB-first, then on-demand live fetch, else raise) ---
    def _bars_cache_key(self, symbol: str, timeframe: str, limit: int) -> str:
        try:
            sym = str(symbol or "").strip().upper()
        except Exception:
            sym = str(symbol)
        try:
            n = max(1, min(int(limit), 1000))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            n = 30
        return f"bars:{sym}:{(timeframe or '1d')}:{n}"

    def get_bars(self, symbol: str, timeframe: str = "1d", limit: int = 30) -> dict:
        """Serve daily bars from ``price_bars`` when coverage is sufficient.

        DB path: resolve via the registry, read the latest ``limit`` rows
        for ``(instrument_id, timeframe)`` ascending; trusted when at least
        ``min(limit, 100)`` rows come back (quality gate so thin histories
        never masquerade as full coverage) AND the latest bar date covers
        the last completed trading session (freshness gate so days-old
        bars are refreshed, never served). Provenance then carries the
        stored source, ``fallback_used=False`` and a ``grade_quality`` grade.

        Fail-closed: thin/empty/stale coverage triggers an on-demand live
        fetch (``1d`` only); when that also yields nothing fresh the request
        raises instead of serving synthetic stub bars or days-old history.
        No fallbacks, ever.
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
            if db_out is not None and self._bars_payload_is_fresh(db_out, symbol, timeframe):
                if self._bars_should_prefer_alpaca_refresh(symbol, timeframe, db_out):
                    # Fresh but wrong feed for a US chart: fall through to
                    # the live Alpaca refresh below instead of serving
                    # yfinance rows under an Alpaca quote.
                    pass
                else:
                    if self.cache is not None and cache_key is not None:
                        try:
                            self.cache.set(cache_key, db_out, ttl_s=120)  # type: ignore[union-attr]
                        except Exception:
                            pass
                    return db_out
            # Stale (not thin): fall through to the live refresh below —
            # days-old bars are never served.
        except Exception as exc:
            logger.debug("bars db path miss for %s: %s", str(symbol)[:16], type(exc).__name__)
        try:
            if self._fetch_and_store_bars(symbol, timeframe):
                db_out = self._get_bars_from_db(symbol, timeframe, limit)
                if db_out is not None and self._bars_payload_is_fresh(db_out, symbol, timeframe):
                    if self.cache is not None and cache_key is not None:
                        try:
                            self.cache.set(cache_key, db_out, ttl_s=120)  # type: ignore[union-attr]
                        except Exception:
                            pass
                    return db_out
        except Exception as exc:
            logger.debug("bars live path miss for %s: %s", str(symbol)[:16], type(exc).__name__)
        # Fail-closed: DB thin/empty/stale and live fetch missed — raise
        # instead of fabricating deterministic stub bars or serving
        # days-old history. Genuinely-short histories (e.g. recent IPOs
        # with k < min(limit,100) rows total) raise ValueError so routers
        # map to 422 insufficient-history; full-but-stale or empty DBs raise
        # ProviderError (502, never serve days-old rows). Total row count
        # distinguishes the two: stale AAPL still holds 100+ old rows.
        try:
            needed = min(int(limit), 100)
        except (TypeError, ValueError):
            needed = 100
        try:
            from backend.db.session import get_session_factory as _gsf

            from backend.db.models import Instrument as _DBI
            from backend.db.models import PriceBar as _PB

            try:
                symbol_text = str(symbol or "").strip()
            except Exception:
                symbol_text = ""
            total = 0
            try:
                instrument, _, _ = self.registry.resolve(symbol_text)
                if instrument is None:
                    try:
                        instrument = _provisional_instrument(symbol_text)
                    except Exception:
                        instrument = None
                if instrument is not None:
                    _S = _gsf()()
                    try:
                        total = (
                            _S.query(_PB)
                            .join(
                                _DBI,
                                _PB.instrument_id == _DBI.instrument_id,
                            )
                            .filter(
                                _DBI.exchange_mic == instrument.exchange_mic,
                                _DBI.exchange_symbol
                                == instrument.exchange_symbol,
                                _PB.timeframe == (timeframe or "1d"),
                            )
                            .count()
                        )
                    finally:
                        try:
                            _S.close()
                        except Exception:
                            pass
            except Exception:
                total = 0
            # V2 resilience: DB thin (e.g. fresh deploy holds 1 TSLA row) but
            # the live chain may still serve. Try one direct live fetch here
            # (bypassing the 300s miss-cache) and serve it immediately so
            # charts never 422 for large caps with deep vendor history.
            # Best-effort DB upsert keeps the next view on the fast path.
            if total > 0 and total < needed:
                try:
                    from backend.market_data.ingest import fetch_daily_bars_with_fallback as _live_fetch
                    from backend.market_data.ingest import _get_or_create_db_instrument as _get_inst
                    from backend.market_data.ingest import _upsert_bars as _upsert
                    from backend.db.session import get_session_factory as _GSF2
                    from backend.db.session import init_db as _init2

                    _live_bars, _live_src = _live_fetch(symbol_text)
                    # Yahoo throttle fallback: Stooq CSV is delayed but far
                    # better than a 422 for large caps (TSLA/MSFT) with deep
                    # vendor history. Mirrors _fetch_and_store_bars.
                    if not _live_bars or len(_live_bars) < min(int(limit or 30), 30):
                        try:
                            from backend.market_data.ingest import fetch_stooq_daily_bars as _stooq_fetch

                            _stooq_bars = _stooq_fetch(symbol_text)
                            if _stooq_bars and len(_stooq_bars) >= min(int(limit or 30), 30):
                                _live_bars, _live_src = _stooq_bars, "stooq"
                        except Exception:
                            pass
                    if _live_bars and len(_live_bars) >= min(int(limit or 30), 30):
                        try:
                            _init2()
                            _S2 = _GSF2()()
                            try:
                                if instrument is not None:
                                    _dbi = _get_inst(_S2, instrument)
                                    _upsert(_S2, _dbi, _live_bars, timeframe=(timeframe or "1d"), source=str(_live_src or "yfinance"))
                                    _S2.commit()
                            finally:
                                try:
                                    _S2.close()
                                except Exception:
                                    pass
                        except Exception:
                            pass
                        # Build the wire payload directly from live bars
                        # (same shape as _bars_response_from_rows, live grade).
                        try:
                            _cut = max(1, min(int(limit or 30), 1000))
                        except Exception:
                            _cut = 30
                        _tail = _live_bars[-_cut:]
                        _rows = []
                        for _b in _tail:
                            try:
                                _ts = _b.get("ts")
                                _ts_iso = _ts.isoformat() if isinstance(_ts, datetime) else str(_ts)
                            except Exception:
                                continue
                            _rows.append({
                                "ts": _ts_iso,
                                "open": _b.get("open"),
                                "high": _b.get("high"),
                                "low": _b.get("low"),
                                "close": _b.get("close"),
                                "volume": _b.get("volume"),
                                "missing_fields": [],
                            })
                        if _rows:
                            try:
                                _mic = str(getattr(instrument, "exchange_mic", None) or "XNAS")
                            except Exception:
                                _mic = "XNAS"
                            try:
                                _exp = expected_delay_minutes(_mic)
                            except Exception:
                                _exp = 15
                            try:
                                _prov = build_provenance(
                                    str(_live_src or "yfinance"), as_of=_utcnow(),
                                    delay_minutes=_exp, quality_grade="C",
                                    fallback_used=False,
                                    missing_fields=[f"live-serve-db-thin-{int(total)}-rows"],
                                ).model_dump(mode="json")
                            except Exception:
                                _prov = {}
                            _payload = {
                                "symbol": symbol_text.upper(),
                                "instrument_id": getattr(instrument, "instrument_id", None) if instrument is not None else None,
                                "timeframe": (timeframe or "1d"),
                                "bars": _rows,
                                "provenance": _prov,
                            }
                            if self.cache is not None and cache_key is not None:
                                try:
                                    self.cache.set(cache_key, _payload, ttl_s=120)
                                except Exception:
                                    pass
                            try:
                                if self.cache is not None:
                                    self.cache.delete(f"barsfetch:{symbol_text.upper()}")
                            except Exception:
                                pass
                            return _payload
                except Exception:
                    pass
                raise ValueError(
                    f"insufficient history for {symbol!r}: "
                    f"only {int(total)} of {limit} bars available "
                    f"(live refresh missed — retry; cron ingest backfills daily)"
                )
        except ValueError:
            raise
        except Exception:
            pass
        from .providers.base import ProviderError as _PE

        raise _PE(
            getattr(self.provider, "name", "market-data"),
            f"no live bars for {symbol} (DB thin/empty/stale, live fetch missed)",
        )

    @staticmethod
    @staticmethod
    def _append_forming_bar(
        rows: list[dict], quote: dict, price: float, quote_day: str,
        as_of_raw: object, mic: str | None,
    ) -> tuple[list[dict], bool, str | None, bool]:
        """Append the quote's session as a forming daily bar (pure helper).

        Every field comes from the quote itself — session open/high/low,
        live close, session volume — so nothing is fabricated. Guards (any
        trip declines with a reason, never a partial bar): finite session
        open required; the quote day must be a trading session when the
        calendar can answer (weekend/holiday stamps never grow candles);
        unknown MICs proceed (nothing disproven). Returns
        ``(rows, stitched, reason, forming)``. Never raises.
        """
        try:
            session_open = float(quote.get("open"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return rows, False, "forming-open-unknown", False
        try:
            import math as _math

            if not _math.isfinite(session_open) or session_open <= 0:
                return rows, False, "forming-open-unknown", False
        except Exception:
            return rows, False, "forming-open-unknown", False
        try:
            mic_up = (mic or "").strip().upper()
        except Exception:
            mic_up = ""
        if mic_up:
            try:
                from datetime import date as _date

                from backend.instruments.calendars import _lib_is_session

                day = _date.fromisoformat(quote_day)
                if _lib_is_session(day, mic_up) is False:
                    return rows, False, "quote-off-session", False
            except Exception:
                pass
        try:
            import math as _math2

            high = session_open
            low = session_open
            try:
                qh = quote.get("high")
                if qh is not None and not isinstance(qh, bool):
                    qh_f = float(qh)  # type: ignore[arg-type]
                    if _math2.isfinite(qh_f) and qh_f > 0:
                        high = max(high, qh_f)
            except (TypeError, ValueError):
                pass
            try:
                ql = quote.get("low")
                if ql is not None and not isinstance(ql, bool):
                    ql_f = float(ql)  # type: ignore[arg-type]
                    if _math2.isfinite(ql_f) and ql_f > 0:
                        low = min(low, ql_f)
                high = max(high, price)
                low = min(low, price)
            except (TypeError, ValueError):
                high = max(high, price)
                low = min(low, price)
            volume = None
            try:
                qvol = quote.get("volume")
                if qvol is not None and not isinstance(qvol, bool):
                    qvol_f = float(qvol)  # type: ignore[arg-type]
                    if _math2.isfinite(qvol_f) and qvol_f >= 0:
                        volume = int(qvol_f)
            except (TypeError, ValueError):
                volume = None
            try:
                ts = str(as_of_raw) if as_of_raw is not None else quote_day
            except Exception:
                ts = quote_day
            rows.append({
                "ts": ts,
                "open": session_open,
                "high": high,
                "low": low,
                "close": price,
                "volume": volume,
                "missing_fields": [],
            })
        except Exception:
            try:
                rows.pop()
            except Exception:
                pass
            return rows, False, "stitch-failed", False
        return rows, True, None, True

    def _stitch_quote_into_bars(
        self, bars: list[dict], quote: dict | None, mic: str | None = None
    ) -> tuple[list[dict], bool, str | None, bool]:
        """Overlay the live quote onto a COPY of 1d bars (pure helper).

        Same-date sessions update the terminal bar's close/high/low (and
        volume when the quote carries a finite one). A newer-session quote
        appends an honest forming bar (every field from the quote; weekend/
        holiday stamps and missing session opens decline). Never mutates
        the input rows (the bars payload may be a shared cache object; DB
        rows are untouched — forecasting keeps clean daily history).
        Returns ``(bars_copy, stitched, reason, forming)``; ``reason`` is
        None when stitched. Never raises.
        """
        try:
            rows = [dict(b) if isinstance(b, dict) else b for b in (bars or [])]
        except Exception:
            return bars, False, "bars-unusable", False
        if not rows:
            return rows, False, "no-bars", False
        if not isinstance(quote, dict):
            return rows, False, "quote-missing", False
        try:
            price = float(quote.get("price"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return rows, False, "quote-priceless", False
        try:
            import math as _math

            if not _math.isfinite(price) or price <= 0:
                return rows, False, "quote-priceless", False
        except Exception:
            return rows, False, "quote-priceless", False
        try:
            as_of_raw = ((quote.get("provenance") or {}).get("as_of")
                         if isinstance(quote.get("provenance"), dict) else None)
            as_of_raw = as_of_raw if as_of_raw is not None else quote.get("as_of")
            quote_day = str(as_of_raw)[:10]
            if len(quote_day) != 10:
                return rows, False, "quote-undated", False
            last = rows[-1]
            last_day = str((last or {}).get("ts") or "")[:10]
            if len(last_day) != 10:
                return rows, False, "bars-undated", False
        except Exception:
            return rows, False, "quote-undated", False
        if quote_day != last_day:
            if quote_day < last_day:
                return rows, False, "quote-stale", False
            return self._append_forming_bar(
                rows, quote, price, quote_day, as_of_raw, mic
            )
        try:
            last["close"] = price
            try:
                if last.get("high") is None or float(last["high"]) < price:
                    last["high"] = price
            except (TypeError, ValueError):
                last["high"] = price
            try:
                if last.get("low") is None or float(last["low"]) > price:
                    last["low"] = price
            except (TypeError, ValueError):
                last["low"] = price
            try:
                qvol = quote.get("volume")
                if qvol is not None and not isinstance(qvol, bool):
                    qvol_f = float(qvol)  # type: ignore[arg-type]
                    import math as _math2

                    if _math2.isfinite(qvol_f) and qvol_f >= 0:
                        last["volume"] = int(qvol_f)
            except (TypeError, ValueError):
                pass
        except Exception:
            return rows, False, "stitch-failed", False
        return rows, True, None, False

    def get_chart(
        self, symbol: str, timeframe: str = "1d", limit: int = 30
    ) -> dict:
        """Single-call chart payload: live quote + bars stitched with it.

        Fetches the bars series (existing DB-first + refresh path, raises
        502 when unservable exactly like :meth:`get_bars`) and the live
        quote (same provider chain) in ONE backend handling, then syncs
        the terminal 1d print with the quote — same-date sessions update
        the last bar in place, newer sessions append an honest forming bar
        (every field from the quote) — so the header price and the chart's
        last print are the same number from the same call. Quote failure
        degrades to ``quote=None`` + ``stitched=False`` (bars still
        served); bars failure raises. Non-1d timeframes skip stitching.
        The stitched rows are response copies — stored bars and the
        forecast engine (which reads :meth:`get_bars`, never this) are
        untouched.
        """
        try:
            symbol_text = str(symbol or "").strip()
        except Exception:
            symbol_text = ""
        bars_payload = self.get_bars(symbol_text, timeframe, limit)
        bars = bars_payload.get("bars", []) if isinstance(bars_payload, dict) else []
        try:
            instrument, _, _ = self.registry.resolve(symbol_text)
            mic = getattr(instrument, "exchange_mic", None) if instrument else None
        except Exception:
            mic = None
        quote: dict | None = None
        if (timeframe or "1d") == "1d":
            try:
                quote = self.get_quote(symbol_text, None)
            except Exception:
                quote = None
        stitched, reason, forming = False, "quote-missing", False
        if quote is not None and (timeframe or "1d") == "1d":
            bars, stitched, reason, forming = self._stitch_quote_into_bars(
                bars, quote, mic
            )
        elif (timeframe or "1d") != "1d":
            reason = "non-1d-timeframe"
        out = dict(bars_payload) if isinstance(bars_payload, dict) else {}
        out["bars"] = bars
        out["quote"] = quote
        out["stitched"] = bool(stitched)
        out["stitched_reason"] = reason
        out["forming"] = bool(forming)
        try:
            final_source = str(
                (bars_payload.get("provenance") or {}).get("source") or ""
            )
        except Exception:
            final_source = ""
        out["alpaca_bars"] = self._alpaca_bars_status(
            symbol_text, timeframe, final_source
        )
        return out

    def _alpaca_bars_status(
        self, symbol_text: str, timeframe: str, final_source: str
    ) -> dict:
        """Why the bars feed is (or isn't) Alpaca — self-serve in /chart.

        Reasons: ``already-alpaca`` (nothing to do), ``non-1d-timeframe``,
        ``non-us-symbol`` (Alpaca is US-only), ``keys-missing`` (set
        ``ALPACA_API_KEY_ID`` + ``ALPACA_API_SECRET_KEY`` on the backend),
        ``cooldown-active`` (a recent Alpaca bars attempt failed — backend
        logs carry the ``alpaca bars miss`` reason; retries resume
        automatically), or ``attempted-unavailable`` (the gate passed but
        the rows still aren't Alpaca — the attempt failed, see logs).
        Never raises.
        """
        try:
            if (timeframe or "1d") != "1d":
                return {"wanted": False, "reason": "non-1d-timeframe"}
            if str(final_source or "").strip().lower() == "alpaca":
                return {"wanted": False, "reason": "already-alpaca"}
            try:
                instrument, _, _ = self.registry.resolve(symbol_text)
            except Exception:
                instrument = None
            try:
                mic = getattr(instrument, "exchange_mic", None) if instrument else None
                psym = ((getattr(instrument, "provider_symbol", None)
                         or symbol_text.upper())
                        if instrument else (symbol_text or "").upper())
            except Exception:
                mic, psym = None, (symbol_text or "").upper()
            if not MarketDataService._is_alpaca_eligible(mic, psym):
                return {"wanted": False, "reason": "non-us-symbol"}
            try:
                from backend.market_data.ingest import _alpaca_keys_present

                if not _alpaca_keys_present():
                    return {"wanted": False, "reason": "keys-missing"}
            except Exception:
                return {"wanted": False, "reason": "keys-missing"}
            try:
                if time.monotonic() < float(
                    getattr(self, "_alpaca_bars_unavailable_until", 0.0) or 0.0
                ):
                    return {"wanted": False, "reason": "cooldown-active"}
            except Exception:
                pass
            return {"wanted": True, "reason": "attempted-unavailable"}
        except Exception:
            return {"wanted": False, "reason": "unknown"}

    def _bars_payload_is_fresh(self, payload: dict | None, symbol: str, timeframe: str) -> bool:
        """Daily-only freshness gate for a bars payload.

        True when the latest bar date covers the last completed trading
        session for the symbol's MIC (weekends expect Friday, pre-open
        Monday expects Friday, post-close Monday expects Monday). True also
        when the verdict cannot be computed (non-``1d`` timeframe, empty /
        unparseable payload, unknown MIC) — those keep legacy behavior and
        never 502 on a mere calendar miss. False ONLY on proven staleness.
        Never raises.
        """
        try:
            if (timeframe or "1d") != "1d":
                return True
            if not isinstance(payload, dict):
                return True
            bars = payload.get("bars")
            if not isinstance(bars, list) or not bars:
                return True
            last = bars[-1]
            if not isinstance(last, dict):
                return True
            raw_ts = last.get("ts")
            if raw_ts is None:
                return True
            try:
                latest_day = str(raw_ts)[:10]
                if len(latest_day) != 10:
                    return True
                from datetime import date as _date

                latest = _date.fromisoformat(latest_day)
            except (TypeError, ValueError):
                return True
            try:
                symbol_text = str(symbol or "").strip()
            except Exception:
                return True
            if not symbol_text:
                return True
            try:
                instrument, _, _ = self.registry.resolve(symbol_text)
            except Exception:
                return True
            if instrument is None:
                # Non-seed valid tickers (GOOGL/SPY/...) resolve via the
                # provisional path so the calendar gate applies instead of
                # serving stale rows as fresh. Unusable text keeps legacy
                # True (never 502 on a calendar miss).
                try:
                    instrument = _provisional_instrument(symbol_text)
                except Exception:
                    instrument = None
                if instrument is None:
                    return True
            try:
                mic = str(instrument.exchange_mic or "").strip().upper()
            except Exception:
                return True
            if not mic:
                return True
            try:
                from backend.instruments.calendars import last_completed_trading_day
            except Exception:
                try:
                    from ..instruments.calendars import last_completed_trading_day  # type: ignore[no-redef]
                except Exception:
                    return True
            try:
                expected = last_completed_trading_day(mic)
            except Exception:
                return True
            if latest >= expected:
                return True
            # Vendor-settle tolerance: Yahoo often publishes yesterday's bar
            # a day late (fetch at 00:00 UTC before US close settles, or
            # delayed vendor feed). If latest is the trading day immediately
            # before expected, serve as fresh instead of 502ing every view
            # until the vendor catches up. >1 day behind stays stale.
            try:
                from datetime import timedelta as _td

                from backend.instruments.calendars import is_trading_day as _is_td

                prev = expected - _td(days=1)
                for _ in range(7):
                    try:
                        if _is_td(prev, mic):
                            break
                    except Exception:
                        break
                    prev -= _td(days=1)
                if latest >= prev:
                    return True
            except Exception:
                pass
            return False
        except Exception:
            return True

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
        self,
        symbols: list[str],
        market: str | None = None,
        *,
        user_id: str | None = None,
        tier: str | None = None,
    ) -> dict[str, dict]:
        """Bulk quotes for a symbol list (bounded pool, per-symbol degrade).

        Never raises; failures are skipped. Homepage/screener fan-outs use
        this instead of N sequential ``get_quote`` calls. ``user_id``/``tier``
        are reserved for future per-user tier routing (passed through to
        :meth:`get_quote`); no-op today.
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
                return sym, self.get_quote(sym, market, user_id=user_id, tier=tier)
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
                    payload = self.get_quote(sym, market, user_id=user_id, tier=tier)
                except Exception:
                    continue
                if isinstance(payload, dict):
                    out[sym] = payload
        return out

    # -- last-fetched persistence (write-through live quotes) --
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
                # instrument_id is a UUID FK (nullable, lineage-only). The
                # registry instrument carries a STRING id ("XNAS-AAPL") which
                # Postgres rejects with 22P02 — so never forward it. Snapshots
                # are keyed by provider symbol; lineage can re-join on
                # (exchange_mic, symbol) when needed. No extra SELECT here:
                # this runs on every live quote (hot path).
                "instrument_id": None,
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
        would be dishonest). Alpaca-covered US symbols fetch Alpaca first
        (same feed as their quotes); anything else — and any Alpaca miss —
        falls back to the yfinance fetch. Misses are negatively cached
        (300s) so an unfetchable symbol does not pay a network timeout on
        every view. An Alpaca miss additionally starts the bars cooldown so
        one slow view per outage is followed by fast ones. Never raises.
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
        # Same-feed bars for Alpaca-covered US symbols: Alpaca first (when
        # eligible AND keys resolve AND no cooldown — all local checks, no
        # network), yfinance otherwise. Non-US paths keep today's
        # yfinance-only behavior exactly (SSE/Euronext never touch Alpaca).
        # Direct attempts (not the generic chain) so an Alpaca miss starts
        # the cooldown while the yfinance cover still serves.
        bars: list[dict] | None = None
        bars_source = "yfinance"
        try:
            mic = getattr(instrument, "exchange_mic", None)
        except Exception:
            mic = None
        alpaca_attempted = False
        if self._alpaca_bars_wanted(mic, provider_symbol):
            alpaca_attempted = True
            try:
                from backend.market_data.ingest import fetch_alpaca_daily_bars

                bars = fetch_alpaca_daily_bars(provider_symbol)
                bars_source = "alpaca"
            except Exception as exc:
                # Visible in backend logs (type + short reason only — key
                # material never flows through exceptions here): repeated
                # lines here mean the Alpaca bars leg is down while quotes
                # may still work (different endpoint/subscription).
                try:
                    logger.warning(
                        "alpaca bars miss symbol=%s reason=%s: %s",
                        provider_symbol,
                        type(exc).__name__,
                        str(exc)[:160],
                    )
                except Exception:
                    pass
                try:
                    self._alpaca_bars_unavailable_until = (
                        time.monotonic() + _ALPACA_BARS_COOLDOWN_S
                    )
                except Exception:
                    pass
                bars = None
        if bars is None:
            # V2: use the ordered chain (alpaca→yfinance when keys present,
            # else yfinance) then Stooq CSV as a last-resort gap-filler so a
            # single-provider Yahoo throttle never leaves TSLA/MSFT with
            # "only 1 of 90 bars available". Stooq daily CSV is delayed but
            # far better than a 422 for large caps with deep history.
            # When the direct Alpaca leg above already failed, skip Alpaca in
            # the chain (single-attempt contract — one slow view per outage).
            # Same when the cooldown is active (direct was skipped): the
            # default chain would otherwise retry Alpaca via its own link.
            try:
                from backend.market_data.ingest import fetch_daily_bars_with_fallback as _chain_fetch

                try:
                    _cooling = time.monotonic() < float(
                        getattr(self, "_alpaca_bars_unavailable_until", 0.0) or 0.0
                    )
                except Exception:
                    _cooling = False
                if alpaca_attempted or _cooling:
                    bars, bars_source = _chain_fetch(provider_symbol, chain=["yfinance"])
                else:
                    bars, bars_source = _chain_fetch(provider_symbol)
            except Exception:
                bars, bars_source = None, "yfinance"
            if not bars:
                try:
                    from backend.market_data.ingest import fetch_stooq_daily_bars as _stooq_fetch

                    bars = _stooq_fetch(provider_symbol)
                    bars_source = "stooq"
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
                _upsert_bars(db, db_inst, bars, timeframe="1d", source=bars_source)
                db.commit()
                # Snapshot-on-fetch: compressed snapshot of exactly what this
                # call sourced (Alpaca-first for US, yfinance cover/stale
                # paths otherwise). Best-effort after bars are durable.
                try:
                    from backend.market_data import snapshot_store as _snapshots

                    _snapshots.save_snapshot(
                        db,
                        symbol=provider_symbol,
                        timeframe="1d",
                        bars=bars,
                        source=str(bars_source or "yfinance"),
                        provenance={"source": str(bars_source or "yfinance"),
                                    "fetch": "on-demand-backfill"},
                        instrument_id=getattr(db_inst, "instrument_id", None),
                        exchange_mic=getattr(instrument, "exchange_mic", None),
                    )
                except Exception:
                    pass
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
        """Read bars from price_bars; None when the DB path must not serve.

        Single round-trip: one ``PriceBar JOIN instruments`` query filtered
        to the latest ``n`` rows (``ts DESC + LIMIT`` then reversed to
        ascending in Python — ``ASC + LIMIT`` would return the *oldest* rows
        instead). The coverage gate (``min(n, 100)`` rows) keeps thin
        histories on the stub path.
        """
        from sqlalchemy import or_ as _or_

        from backend.db.models import Instrument as DBInstrument
        from backend.db.models import PriceBar
        from backend.db.session import get_session_factory

        try:
            symbol_text = str(symbol or "").strip()
        except Exception:
            return None
        try:
            instrument, _, _ = self.registry.resolve(symbol_text)
        except Exception:
            return None
        try:
            n = max(1, min(int(limit), 1000))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            n = 30
        try:
            Session = get_session_factory()  # lazy per call; cached engine
            db = Session()
        except Exception:
            logger.debug("bars db unavailable for %s", symbol_text[:16])
            return None
        try:
            query = (
                db.query(PriceBar, DBInstrument)
                .join(DBInstrument, PriceBar.instrument_id == DBInstrument.instrument_id)
                .filter(PriceBar.timeframe == timeframe)
            )
            if instrument is not None:
                query = query.filter(
                    DBInstrument.exchange_mic == instrument.exchange_mic,
                    DBInstrument.exchange_symbol == instrument.exchange_symbol,
                )
                response_symbol = instrument.provider_symbol
                response_inst_id = instrument.instrument_id
            else:
                # Auto-provisioned symbols (first seen via on-demand
                # backfill): locate by provider symbol directly.
                upper = symbol_text.upper()
                query = query.filter(
                    _or_(
                        DBInstrument.provider_symbol == upper,
                        DBInstrument.exchange_symbol == upper,
                    )
                )
                response_symbol = symbol_text.upper()
                response_inst_id = None
            pairs = query.order_by(PriceBar.ts.desc()).limit(n).all()
            if not pairs:
                return None
            if len(pairs) < min(n, 100):
                return None
            first_inst = pairs[0][1]
            if instrument is None and first_inst is not None:
                try:
                    response_symbol = first_inst.provider_symbol or response_symbol
                    response_inst_id = str(first_inst.instrument_id)
                except Exception:
                    pass
            bar_rows = [bar for bar, _inst in reversed(pairs)]
            return self._bars_response_from_rows(
                bar_rows,
                db_inst=first_inst,
                response_symbol=response_symbol,
                response_inst_id=response_inst_id,
                timeframe=timeframe,
            )
        except Exception:
            logger.debug("bars db query miss for %s", symbol_text[:16] if 'symbol_text' in dir() else "?")
            return None
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
        """Legacy two-query tail (kept for tests); prefers the join path."""
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
        return MarketDataService._bars_response_from_rows(
            rows,
            db_inst=db_inst,
            response_symbol=response_symbol,
            response_inst_id=response_inst_id,
            timeframe=timeframe,
        )

    @staticmethod
    def _bars_response_from_rows(
        rows, *, db_inst, response_symbol: str, response_inst_id,
        timeframe: str,
    ) -> dict | None:
        """Build the bars payload from ascending PriceBar rows (shared tail)."""
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
        try:
            mic = (getattr(db_inst, "exchange_mic", None) or "XNAS")
        except Exception:
            mic = "XNAS"
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


