"""Finnhub provider (free-tier US quotes, key required).

Endpoint (one call per quote)::

    GET https://finnhub.io/api/v1/quote?symbol=AAPL&token=<key>

Response (``/quote``)::

    {"c": 233.10, "d": 2.35, "dp": 1.02, "h": 233.80,
     "l": 230.10, "o": 231.00, "pc": 230.75, "t": 1757683800}

``c`` = current price, ``o``/``h``/``l`` = day open/high/low,
``pc`` = previous close, ``t`` = unix timestamp (UTC) used for ``as_of``.
The endpoint carries NO volume — it stays missing (never fabricated) and
is flagged in ``missing_fields`` by normalization.

Free-tier scope (verified 2026-09, see docs/DATA_SOURCES_FREE.md):
60 calls/minute, real-time **US** quotes. International markets on the
free tier are end-of-day only, so this provider is US-only
(XNYS/XNAS, bare tickers) — the service chain never routes SSE
(``.SS``) or Euronext (``.PA`` / ``.AS`` / ``.BR``) here. Non-US symbols
fail fast to the flagged stub path so the chain falls through to
yfinance/stooq — Finnhub never serves a non-US quote.

Free-tier license is personal use; commercial/redistribution use needs
Finnhub's written approval (ToS note in docs/DATA_SOURCES_FREE.md).

Same resilience contract as alpaca/stooq: per-provider circuit
breaker, token-bucket limiter sized to the free tier (60/min),
tenacity retry on transient failures, deterministic offline stub
fallback (flagged ``fallback_used=True``) so the API stays usable on
outage. Missing keys / network failure degrade to the stub, never
raise into the chain (except empty symbols, which keep the
ProviderError contract). Key material never appears in quotes.

Provenance carries ``source=finnhub`` + ``delay_minutes=0`` for live US
quotes (real-time feed, like Alpaca IEX — single-source, grade B via
the standard quality path, NOT grade A).
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

from .base import CircuitBreaker, ProviderError, RateLimiter

NAME = "finnhub"
DEFAULT_DELAY_MINUTES = 0  # free tier is real-time for US (this provider is US-only)
BASE_URL = "https://finnhub.io/api/v1"

#: Yahoo suffixes Finnhub-free cannot serve (US-only on the free tier;
#: international is EOD-only and stays on yfinance/stooq).
_NON_US_SUFFIXES: tuple[str, ...] = (".SS", ".PA", ".AS", ".BR", ".CN", ".FR", ".NL", ".BE")

#: Deterministic offline reference quotes (outage fallback / forced-stub
#: mode / missing-keys mode only; always served with fallback_used=True).
STUB_QUOTES: dict[str, dict] = {
    "AAPL": {"price": 232.50, "open": 231.00, "high": 233.80, "low": 230.10,
             "prev_close": 230.75, "volume": 54_000_000, "currency": "USD"},
    "MSFT": {"price": 428.15, "open": 426.00, "high": 429.90, "low": 425.20,
             "prev_close": 425.60, "volume": 21_000_000, "currency": "USD"},
    "NVDA": {"price": 131.88, "open": 130.50, "high": 132.70, "low": 129.90,
             "prev_close": 129.40, "volume": 240_000_000, "currency": "USD"},
    "JPM": {"price": 224.30, "open": 223.10, "high": 225.00, "low": 222.40,
            "prev_close": 222.90, "volume": 9_500_000, "currency": "USD"},
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _first_env(*names: str) -> str:
    for name in names:
        try:
            value = (os.getenv(name, "") or "").strip()
        except Exception:
            value = ""
        if value:
            return value
    return ""


def resolve_key(api_key: str | None = None) -> str:
    """Resolve the Finnhub token (arg wins, then env). Never logs the value."""
    return (api_key or "").strip() or _first_env("FINNHUB_API_KEY", "FINNHUB_TOKEN")


def to_finnhub_symbol(symbol: str) -> str:
    """Normalize a US ticker to Finnhub form (``brk-b`` -> ``BRK.B``).

    Raises ProviderError for empty input (retryable=False) and for
    non-US suffixed symbols (retryable=False — no retry, fall through).
    """
    text = (symbol or "").strip().upper()
    if not text:
        raise ProviderError(NAME, "empty symbol", retryable=False)
    for suffix in _NON_US_SUFFIXES:
        if text.endswith(suffix):
            raise ProviderError(NAME, "finnhub free tier supports US symbols only",
                                retryable=False)
    # Yahoo uses BRK-B; Finnhub uses BRK.B.
    core = text.replace("-", ".")
    if not core or core.strip(".") == "":
        raise ProviderError(NAME, "empty symbol", retryable=False)
    return core


def _coerce_float(value: object) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    # Finnhub uses 0 as "no data" for o/h/l/pc; only c==0 means no quote.
    if number == 0:
        return None
    return number


def _parse_time(value: object) -> datetime:
    try:
        stamp = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return _utcnow()
    if stamp != stamp or stamp in (float("inf"), float("-inf")) or stamp <= 0:
        return _utcnow()
    try:
        return datetime.fromtimestamp(stamp, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return _utcnow()


def parse_quote(symbol: str, payload: dict) -> dict:
    """Parse a Finnhub ``/quote`` payload into a raw quote dict.

    Raises ProviderError when no usable price exists (never fabricates).
    Volume is always None (the endpoint carries none). Never includes
    key material.
    """
    if not isinstance(payload, dict):
        raise ProviderError(NAME, f"no data for {symbol}")
    price = _coerce_float(payload.get("c"))
    if price is None:
        raise ProviderError(NAME, f"no data for {symbol}")
    return {
        "symbol": (symbol or "").strip().upper(),
        "price": price,
        "open": _coerce_float(payload.get("o")),
        "high": _coerce_float(payload.get("h")),
        "low": _coerce_float(payload.get("l")),
        "prev_close": _coerce_float(payload.get("pc")),
        "volume": None,  # /quote carries no volume; flagged missing downstream
        "currency": "USD",
        "as_of": _parse_time(payload.get("t")),
    }


class FinnhubProvider:
    """Live Finnhub provider (free tier, US, real-time) with stub fallback."""

    name = NAME

    def __init__(
        self,
        *,
        api_key: str | None = None,
        breaker: CircuitBreaker | None = None,
        limiter: RateLimiter | None = None,
        delay_minutes: int = DEFAULT_DELAY_MINUTES,
        stub_mode: bool = False,
        on_call: object | None = None,
        timeout_s: float = 60.0,
    ) -> None:
        self.breaker = breaker or CircuitBreaker()
        # Free tier: 60 calls/minute -> ~1 rps, small burst.
        self.limiter = limiter or RateLimiter(rate_per_sec=1.0, burst=5)
        self.delay_minutes = delay_minutes
        self.stub_mode = stub_mode
        self.timeout_s = timeout_s
        self._on_call = on_call  # health hook: fn(provider, latency_ms, ok)
        self._api_key = resolve_key(api_key)

    # -- config ---------------------------------------------------------
    @property
    def configured(self) -> bool:
        """True when a token is present (value never exposed)."""
        return bool(self._api_key)

    # -- internals ------------------------------------------------------
    def _emit(self, latency_ms: float, ok: bool, *, error=None, status_code=None) -> None:
        try:
            from .base import emit_health as _emit_health
        except Exception:
            from backend.market_data.providers.base import emit_health as _emit_health  # type: ignore[no-redef]

        _emit_health(self._on_call, self.name, latency_ms, ok,
                     error=error, status_code=status_code)

    def _stub_quote(self, symbol: str) -> dict:
        from ..normalization import normalize_quote  # local import: no cycle

        upper = (symbol or "").strip().upper()
        base = dict(STUB_QUOTES.get(upper, {"price": 100.0, "currency": "USD"}))
        base["currency"] = "USD"  # US-only feed never serves another ccy
        base.update({"symbol": upper, "as_of": _utcnow(), "fallback": True})
        quote = normalize_quote(base, source=self.name, delay_minutes=self.delay_minutes)
        quote["currency"] = "USD"
        if "currency" in quote.get("missing_fields", []):
            quote["missing_fields"] = [f for f in quote["missing_fields"] if f != "currency"]
        quote["fallback_used"] = True
        return quote

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_fixed(1),
        retry=retry_if_exception_type(ProviderError),
        reraise=True,
    )
    def _fetch_raw(self, symbol: str, market: str | None = None) -> dict:
        _ = market  # accepted for call-site parity; US-only feed
        if not self.configured:
            raise ProviderError(NAME, "finnhub API key missing")
        try:
            import httpx  # lazy: offline/test envs fall back to stub
        except Exception as exc:
            raise ProviderError(NAME, "httpx package unavailable") from exc
        upper = (symbol or "").strip().upper()
        if not upper:
            raise ProviderError(NAME, "empty symbol", retryable=False)
        finnhub_symbol = to_finnhub_symbol(upper)  # non-US -> ProviderError
        url = f"{BASE_URL}/quote"
        try:
            resp = httpx.get(
                url,
                params={"symbol": finnhub_symbol, "token": self._api_key},
                timeout=self.timeout_s,
            )
        except Exception as exc:  # network / timeout
            raise ProviderError(NAME, f"{type(exc).__name__}: {exc}") from exc
        try:
            status = int(getattr(resp, "status_code", 200) or 200)
        except (TypeError, ValueError):
            status = 200
        if status in (401, 403):
            raise ProviderError(NAME, "unauthorized (check Finnhub token)",
                                retryable=False)
        if status == 404:
            raise ProviderError(NAME, f"no data for {upper}", retryable=False)
        if status == 429:
            raise ProviderError(NAME, "rate limited (429)")
        if status >= 400:
            raise ProviderError(NAME, f"HTTP {status}")
        try:
            payload = resp.json()
        except Exception as exc:
            raise ProviderError(NAME, f"{type(exc).__name__}: {exc}") from exc
        raw = parse_quote(upper, payload if isinstance(payload, dict) else {})
        raw["currency"] = "USD"
        return raw

    # -- public ---------------------------------------------------------
    def get_quote(self, symbol: str, market: str | None = None) -> dict:
        from ..normalization import normalize_quote

        upper = (symbol or "").strip().upper()
        if not upper:
            raise ProviderError(self.name, "empty symbol", retryable=False)

        if self.stub_mode:
            quote = self._stub_quote(upper)
            self._emit(0.0, True)
            return quote

        if not self.breaker.allow_request():
            quote = self._stub_quote(upper)
            quote["circuit_open"] = True
            self._emit(0.0, False, error="circuit open (breaker)")
            return quote

        self.limiter.acquire()  # stub: counted, never blocks local run
        started = time.perf_counter()
        try:
            raw = self._fetch_raw(upper, market)
        except ProviderError as exc:
            self.breaker.record_failure()
            self._emit((time.perf_counter() - started) * 1000, False,
                       error=f"{type(exc).__name__}: {exc}")
            quote = self._stub_quote(upper)
            quote["circuit_open"] = self.breaker.state != CircuitBreaker.CLOSED
            return quote
        self.breaker.record_success()
        self._emit((time.perf_counter() - started) * 1000, True)
        raw["currency"] = "USD"
        quote = normalize_quote(raw, source=self.name, delay_minutes=self.delay_minutes)
        quote["currency"] = "USD"
        if "currency" in quote.get("missing_fields", []):
            quote["missing_fields"] = [f for f in quote["missing_fields"] if f != "currency"]
        quote["fallback_used"] = False
        return quote
