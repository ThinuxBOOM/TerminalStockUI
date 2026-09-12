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
        instrument, candidates, ambiguous = self.registry.resolve(symbol, market)
        provider_symbol = instrument.provider_symbol if instrument else symbol.strip().upper()
        mic = instrument.exchange_mic if instrument else (
            market.strip().upper() if market and market.strip() else "XNAS"
        )

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
        cache_key = f"quote:{provider_symbol}"
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
            quote = self.provider.get_quote(provider_symbol)
        as_of = quote.get("as_of") or _utcnow()
        if not isinstance(as_of, datetime):
            as_of = _utcnow()
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
        age_min = max(0.0, (_utcnow() - as_of).total_seconds() / 60)
        fallback = bool(quote.pop("fallback_used", False))
        grade, _reasons = grade_quality(
            delay_minutes=expected,
            age_minutes=age_min,
            missing_fields=quote.get("missing_fields", []),
            fallback_used=fallback,
            reconciled=False,  # single source in v1
        )
        provenance = build_provenance(
            quote.pop("source", self.provider.name),
            as_of=as_of,
            delay_minutes=expected,
            quality_grade=grade,
            fallback_used=fallback,
            missing_fields=quote.get("missing_fields", []),
        )
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
            "market_state": market_state(as_of, delay_minutes=expected),
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

    # -- bars (deterministic offline-capable stub) ----------------------
    def get_bars(self, symbol: str, timeframe: str = "1d", limit: int = 30) -> dict:
        instrument, _, _ = self.registry.resolve(symbol)
        provider_symbol = instrument.provider_symbol if instrument else symbol.strip().upper()
        seed = int(hashlib.sha256(provider_symbol.encode()).hexdigest(), 16) % (2**32)
        rng = random.Random(seed)
        base = 100.0 + (seed % 900)
        price = base
        rows: list[dict] = []
        day = _utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        for i in range(max(1, min(int(limit), 250))):
            drift = rng.uniform(-0.015, 0.015)
            o = price
            c = round(o * (1 + drift), 2)
            h = round(max(o, c) * (1 + rng.uniform(0, 0.008)), 2)
            low = round(min(o, c) * (1 - rng.uniform(0, 0.008)), 2)
            rows.append({
                "ts": (day - timedelta(days=(limit - 1 - i))).isoformat(),
                "open": round(o, 2), "high": h, "low": low, "close": c,
                "volume": rng.randint(100_000, 60_000_000),
                "missing_fields": [],
            })
            price = c
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
