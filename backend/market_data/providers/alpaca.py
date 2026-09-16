"""Alpaca provider (free real-time US via the IEX feed).

Endpoint (one call per quote)::

    GET https://data.alpaca.markets/v2/stocks/{SYM}/snapshot?feed=iex

Auth is header-only at call time (never logged, never returned)::

    APCA-API-KEY-ID: <key id>
    APCA-API-SECRET-KEY: <secret>

Keys resolve from constructor args or the environment (first hit wins):

- key id: ``ALPACA_API_KEY_ID`` / ``APCA_API_KEY_ID`` / ``ALPACA_API_KEY``
- secret: ``ALPACA_API_SECRET_KEY`` / ``APCA_API_SECRET_KEY`` / ``ALPACA_SECRET_KEY``
- feed: ``ALPACA_FEED`` (``iex`` free default; ``sip`` when subscribed)

Scope: US only (XNYS/XNAS, bare tickers). Non-US symbols (``.SS`` /
``.PA`` / ``.AS`` / ``.BR``) fail fast to the flagged stub path so the
service chain can fall through to yfinance/stooq — Alpaca never serves
a non-US quote.

Free IEX is a single-exchange feed (not full NBBO/SIP): provenance
carries ``source=alpaca`` + ``delay_minutes=0`` so the UI can badge it
as live-but-partial. Missing keys / network failure degrade to the
deterministic stub (``fallback_used=True``), never raise into the chain
(except empty symbols, which keep the ProviderError contract).
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

from .base import CircuitBreaker, ProviderError, QuotaLimiter, RateLimiter

NAME = "alpaca"
DEFAULT_DELAY_MINUTES = 0
BASE_URL = "https://data.alpaca.markets"

#: Yahoo suffixes Alpaca cannot serve (US-only feed).
_NON_US_SUFFIXES: tuple[str, ...] = (".SS", ".PA", ".AS", ".BR", ".CN", ".FR", ".NL", ".BE", ".BO", ".L")

_ALLOWED_FEEDS = ("iex", "sip", "delayed_sip")

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


def resolve_keys(
    api_key: str | None = None, api_secret: str | None = None
) -> tuple[str, str]:
    """Resolve Alpaca credentials (args win, then env). Never logs values."""
    key = (api_key or "").strip() or _first_env(
        "ALPACA_API_KEY_ID", "APCA_API_KEY_ID", "ALPACA_API_KEY"
    )
    secret = (api_secret or "").strip() or _first_env(
        "ALPACA_API_SECRET_KEY", "APCA_API_SECRET_KEY", "ALPACA_SECRET_KEY"
    )
    return key, secret


def resolve_feed(feed: str | None = None) -> str:
    """Resolve the Alpaca feed (``iex`` free default)."""
    raw = ((feed or "").strip() or _first_env("ALPACA_FEED") or "iex").strip().lower()
    return raw if raw in _ALLOWED_FEEDS else "iex"


def to_alpaca_symbol(symbol: str) -> str:
    """Normalize a US ticker to Alpaca form (``brk-b`` -> ``BRK.B``).

    Raises ProviderError for empty input (retryable=False) and for
    non-US suffixed symbols (retryable=False — no retry, fall through).
    """
    text = (symbol or "").strip().upper()
    if not text:
        raise ProviderError(NAME, "empty symbol", retryable=False)
    for suffix in _NON_US_SUFFIXES:
        if text.endswith(suffix):
            raise ProviderError(NAME, "alpaca supports US symbols only", retryable=False)
    # Yahoo uses BRK-B; Alpaca uses BRK.B.
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
    return number


def _coerce_int(value: object) -> int | None:
    number = _coerce_float(value)
    if number is None:
        return None
    try:
        return int(number)
    except (TypeError, ValueError, OverflowError):
        return None


def _parse_time(value: object) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    try:
        text = str(value or "").strip()
    except Exception:
        return _utcnow()
    if not text:
        return _utcnow()
    try:
        # Alpaca RFC3339: 2026-09-12T14:30:00.123456789Z
        iso = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return _utcnow()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def parse_snapshot(symbol: str, payload: dict) -> dict:
    """Parse an Alpaca snapshot payload into a raw quote dict.

    Raises ProviderError when no usable price exists (never fabricates).
    Never includes key material.
    """
    if not isinstance(payload, dict):
        raise ProviderError(NAME, f"no data for {symbol}")
    trade = payload.get("latestTrade") if isinstance(payload.get("latestTrade"), dict) else {}
    daily = payload.get("dailyBar") if isinstance(payload.get("dailyBar"), dict) else {}
    prev = payload.get("prevDailyBar") if isinstance(payload.get("prevDailyBar"), dict) else {}
    assert isinstance(trade, dict) and isinstance(daily, dict) and isinstance(prev, dict)
    price = _coerce_float(trade.get("p"))
    if price is None:
        price = _coerce_float(daily.get("c"))
    if price is None:
        raise ProviderError(NAME, f"no data for {symbol}")
    as_of = _parse_time(trade.get("t") or daily.get("t"))
    return {
        "symbol": (symbol or "").strip().upper(),
        "price": price,
        "open": _coerce_float(daily.get("o")),
        "high": _coerce_float(daily.get("h")),
        "low": _coerce_float(daily.get("l")),
        "prev_close": _coerce_float(prev.get("c")),
        "volume": _coerce_int(daily.get("v")),
        "currency": "USD",
        "as_of": as_of,
    }


class AlpacaProvider:
    """Live Alpaca snapshot provider (US, IEX free) with stub fallback."""

    name = NAME

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
        feed: str | None = None,
        breaker: CircuitBreaker | None = None,
        limiter: RateLimiter | QuotaLimiter | None = None,
        delay_minutes: int = DEFAULT_DELAY_MINUTES,
        stub_mode: bool = False,
        on_call: object | None = None,
        timeout_s: float = 12.0,
    ) -> None:
        self.breaker = breaker or CircuitBreaker()
        # Alpaca free is generous (200/min) — cap at 150/min client-side so
        # bursts fall through to yfinance instead of tripping the vendor.
        self.limiter = limiter or QuotaLimiter(
            calls_per_minute=150, name="alpaca")
        self.delay_minutes = delay_minutes
        self.stub_mode = stub_mode
        self.timeout_s = timeout_s
        self._on_call = on_call  # health hook: fn(provider, latency_ms, ok)
        self._api_key, self._api_secret = resolve_keys(api_key, api_secret)
        self.feed = resolve_feed(feed)

    # -- config ---------------------------------------------------------
    @property
    def configured(self) -> bool:
        """True when key id + secret are both present (no values exposed)."""
        return bool(self._api_key and self._api_secret)

    # -- internals ------------------------------------------------------
    def _emit(self, latency_ms: float, ok: bool, *, error=None, status_code=None) -> None:
        from .base import emit_health as _emit_health

        _emit_health(self._on_call, self.name, latency_ms, ok,
                     error=error, status_code=status_code)

    def _acquire_quota(self) -> bool:
        """Client-side quota gate (False when over cap). Never raises."""
        try:
            acquire = getattr(self.limiter, "acquire", None)
            if acquire is None:
                return True
            return bool(acquire())
        except Exception:
            return True

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
            raise ProviderError(NAME, "alpaca API keys missing")
        try:
            import httpx  # lazy: offline/test envs fall back to stub
        except Exception as exc:
            raise ProviderError(NAME, "httpx package unavailable") from exc
        upper = (symbol or "").strip().upper()
        if not upper:
            raise ProviderError(NAME, "empty symbol", retryable=False)
        alpaca_symbol = to_alpaca_symbol(upper)  # non-US -> ProviderError, no retry loop waste beyond tenacity
        url = f"{BASE_URL}/v2/stocks/{alpaca_symbol}/snapshot"
        headers = {
            "APCA-API-KEY-ID": self._api_key,
            "APCA-API-SECRET-KEY": self._api_secret,
        }
        try:
            resp = httpx.get(
                url, params={"feed": self.feed}, headers=headers, timeout=self.timeout_s
            )
        except Exception as exc:  # network / timeout
            raise ProviderError(NAME, f"{type(exc).__name__}: {exc}") from exc
        try:
            status = int(getattr(resp, "status_code", 200) or 200)
        except (TypeError, ValueError):
            status = 200
        if status in (401, 403):
            raise ProviderError(NAME, "unauthorized (check Alpaca keys)", retryable=False)
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
        raw = parse_snapshot(upper, payload if isinstance(payload, dict) else {})
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

        if not self._acquire_quota():
            # Client-side cap (150/min): fail fast with a 429-style error so
            # the service chain falls through to yfinance. Deliberately NO
            # health emit and NO stub (see twelvedata: throttling is routine
            # flow-control, not provider illness).
            import logging as _logging

            _logging.getLogger(__name__).debug(
                "alpaca client-side quota hit; falling through")
            raise ProviderError(
                NAME, "rate limited by client-side quota (HTTP 429)")
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
