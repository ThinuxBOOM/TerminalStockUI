"""Quote service: registry -> provider -> normalize -> quality -> provenance.

Single place where the provenance envelope is attached to market data.
Fail-closed: only live provider data is ever served. When no provider in
the chain serves a live quote (or no live bars exist), the request raises
ProviderError — routers map that to 502/503. No stubs, no snapshots-as-cover,
no stale data. Ever.
"""

from __future__ import annotations

import hashlib
import random
from datetime import datetime, timedelta, timezone
from functools import lru_cache

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

try:  # Milestone 0 live-data extension (opt-in; None when unavailable)
    from backend.market_data.providers.alpaca import AlpacaProvider
except ImportError:  # pragma: no cover
    try:
        from .providers.alpaca import AlpacaProvider  # type: ignore[no-redef]
    except ImportError:
        AlpacaProvider = None  # type: ignore[assignment]

try:  # Milestone 0 delayed gap-filler (opt-in; None when unavailable)
    from backend.market_data.providers.stooq import StooqProvider
except ImportError:  # pragma: no cover
    try:
        from .providers.stooq import StooqProvider  # type: ignore[no-redef]
    except ImportError:
        StooqProvider = None  # type: ignore[assignment]

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
        # SSE secondary (AKShare). Independent breaker/limiter -> failure isolation.
        # Default inherits stub_mode from primary so offline/test services stay offline.
        if akshare_provider is not None:
            self.akshare_provider = akshare_provider
        elif AKShareProvider is not None:
            primary_stub = bool(getattr(self.provider, "stub_mode", False))
            try:
                self.akshare_provider = AKShareProvider(
                    on_call=lambda p, ms, ok, **kw: self.health.record(p, ms, ok, status_code=kw.get("status_code"), error=kw.get("error")),
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
                    lambda p, ms, ok, **kw: self.health.record(p, ms, ok, status_code=kw.get("status_code"), error=kw.get("error"))
                )
            except Exception:
                pass
        # Milestone 0 live-data chain (opt-in, additive — default None keeps
        # every existing call site on the yfinance/AKShare behavior).
        # deps.get_market_service() wires real instances; tests pass explicit
        # stub/live doubles. No auto-create here: auto-creating a networked
        # Stooq inside every MarketDataService() would turn pure-outage tests
        # into live-data tests and add network latency to hermetic suites.
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
        self.stooq_provider = stooq_provider
        if self.stooq_provider is not None and getattr(
            self.stooq_provider, "_on_call", None
        ) is None:
            try:
                self.stooq_provider._on_call = (  # type: ignore[union-attr]
                    lambda p, ms, ok, **kw: self.health.record(p, ms, ok, status_code=kw.get("status_code"), error=kw.get("error"))
                )
            except Exception:
                pass
        # Free-tier US redundancy (additive, same pattern): Finnhub +
        # TwelveData sit between yfinance and stooq in the live chain.
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
        for suffix in (".SS", ".PA", ".AS", ".BR"):
            if upper.endswith(suffix):
                return False
        return True

    @staticmethod
    def _is_stooq_eligible(mic: str | None, provider_symbol: str) -> bool:
        """Stooq covers US + Euronext; SSE stays on yfinance/AKShare."""
        try:
            upper_mic = (mic or "").strip().upper()
        except Exception:
            upper_mic = ""
        if upper_mic == "XSHG":
            return False
        upper = (provider_symbol or "").strip().upper()
        if upper.endswith(".SS"):
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
    # ignored): Free -> keyless only (yfinance/AKShare/Stooq); Silver ->
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
            # SSE live chain: yfinance(.SS) -> akshare(6-digit), first LIVE
            # quote wins. Fail-closed: when neither provider serves live data
            # the request raises instead of serving a synthetic stub — no
            # fallbacks, no stale snapshots, no fabricated prices.
            # Each provider has an independent breaker (isolation).
            from .providers.base import ProviderError as _PE

            yahoo_symbol = _to_yahoo_sse_symbol(provider_symbol)
            ak_code = _to_akshare_code(yahoo_symbol)
            quote: dict | None = None
            try:
                q_yf = self.provider.get_quote(yahoo_symbol)
            except Exception as exc:
                if isinstance(exc, _PE) and "empty symbol" in str(exc).lower():
                    raise
                q_yf = None
            if _quote_is_live(q_yf):
                quote = q_yf
            else:
                q_ak = self._call_akshare(ak_code)
                if _quote_is_live(q_ak):
                    quote = q_ak
            if quote is None:
                raise _PE(
                    getattr(self.provider, "name", "yfinance"),
                    f"no live quote for {yahoo_symbol} (yfinance+akshare unavailable)",
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
            # twelvedata live (US free, delay 0) -> stooq (delay 15,
            # US+Euronext) -> snapshot/stub. First live quote wins (alpaca
            # preferred = freshest) with short-circuit so one live feed
            # costs one call. When nothing is live the request raises
            # (fail-closed — see below). Absent providers (None) are
            # skipped, so every existing call site without explicit wiring
            # behaves as before.
            q_alpaca: dict | None = None
            q_yf_chain: dict | None = None
            q_finnhub: dict | None = None
            q_twelvedata: dict | None = None
            q_stooq: dict | None = None
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
            if quote is None and self._is_stooq_eligible(mic, provider_symbol):
                q_stooq = self._call_chain_provider(
                    self.stooq_provider, provider_symbol, mic
                )
                if _quote_is_live(q_stooq):
                    quote = q_stooq
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
        never masquerade as full coverage). Provenance then carries the
        stored source, ``fallback_used=False`` and a ``grade_quality`` grade.

        Fail-closed: thin/empty coverage triggers an on-demand live fetch
        (``1d`` only); when that also yields nothing the request raises
        instead of serving synthetic stub bars. No fallbacks, ever.
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
        # Fail-closed: DB thin/empty and live fetch missed — raise instead
        # of fabricating deterministic stub bars. Routers map this to 502.
        from .providers.base import ProviderError as _PE

        raise _PE(
            getattr(self.provider, "name", "market-data"),
            f"no live bars for {symbol} (DB thin/empty, live fetch missed)",
        )

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
        instrument, _, _ = self.registry.resolve(symbol_text)
        try:
            n = max(1, min(int(limit), 1000))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            n = 30
        Session = get_session_factory()  # lazy per call; cached engine
        db = Session()
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
            if len(pairs) < min(n, 100):
                return None
            if not pairs:
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


