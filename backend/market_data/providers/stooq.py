"""Stooq provider (free delayed gap-filler for US + Euronext).

No API key. Single CSV snapshot per symbol::

    https://stooq.com/q/l/?s=aapl.us&f=sd2t2ohlcv&h&e=csv

Coverage: US (``.us``) + Euronext Paris/Amsterdam/Brussels
(``.fr`` / ``.nl`` / ``.be``). SSE (``.SS``) is intentionally NOT routed
here (service chain skips Stooq for XSHG) — Yahoo ``.SS`` + AKShare own SSE.

Same resilience contract as yfinance/akshare: per-provider circuit
breaker, token-bucket limiter, tenacity retry on transient failures,
deterministic offline stub fallback (flagged ``fallback_used=True``,
grade C downstream) so the API stays usable on outage.

``as_of`` is the fetch time (UTC). Stooq returns an exchange-local
date/time without a zone; trusting it as UTC once mislabeled quotes, so
we never parse it as a timestamp. ``prev_close`` is left missing (never
fabricated) — normalization flags it in ``missing_fields``.
"""

from __future__ import annotations

import csv
import io
import time
from datetime import datetime, timezone

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

from .base import CircuitBreaker, ProviderError, RateLimiter

NAME = "stooq"
DEFAULT_DELAY_MINUTES = 15
BASE_URL = "https://stooq.com/q/l/"

#: Yahoo-suffix -> Stooq suffix (lowercase). Bare US tickers -> ``.us``.
_YAHOO_SUFFIX_TO_STOOQ: dict[str, str] = {
    ".PA": ".fr",
    ".AS": ".nl",
    ".BR": ".be",
    ".SS": ".cn",
    "": ".us",
}

#: MIC -> Stooq suffix (used for bare symbols when ``market``/mic is known).
_MIC_TO_STOOQ: dict[str, str] = {
    "XNYS": ".us",
    "XNAS": ".us",
    "XPAR": ".fr",
    "XAMS": ".nl",
    "XBRU": ".be",
    "XSHG": ".cn",
}

#: Stooq suffix -> quote currency.
_STOOQ_SUFFIX_TO_CCY: dict[str, str] = {
    ".us": "USD",
    ".fr": "EUR",
    ".nl": "EUR",
    ".be": "EUR",
    ".cn": "CNY",
}

#: Deterministic offline reference quotes (outage fallback / forced-stub
#: mode only; always served with fallback_used=True, never as live prices).
STUB_QUOTES: dict[str, dict] = {
    "AAPL": {"price": 232.50, "open": 231.00, "high": 233.80, "low": 230.10,
             "prev_close": 230.75, "volume": 54_000_000, "currency": "USD"},
    "MSFT": {"price": 428.15, "open": 426.00, "high": 429.90, "low": 425.20,
             "prev_close": 425.60, "volume": 21_000_000, "currency": "USD"},
    "NVDA": {"price": 131.88, "open": 130.50, "high": 132.70, "low": 129.90,
             "prev_close": 129.40, "volume": 240_000_000, "currency": "USD"},
    "MC.PA": {"price": 715.50, "open": 712.00, "high": 718.20, "low": 710.40,
              "prev_close": 711.30, "volume": 480_000, "currency": "EUR"},
    "ASML.AS": {"price": 812.30, "open": 808.00, "high": 815.60, "low": 805.10,
                "prev_close": 806.90, "volume": 620_000, "currency": "EUR"},
    "UCB.BR": {"price": 168.40, "open": 167.20, "high": 169.10, "low": 166.80,
               "prev_close": 166.95, "volume": 310_000, "currency": "EUR"},
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_stooq_symbol(symbol: str, market: str | None = None) -> str:
    """Normalize a Yahoo-style symbol to a Stooq symbol (``AAPL`` -> ``aapl.us``).

    Raises ProviderError on empty input (retryable=False).
    """
    text = (symbol or "").strip().upper()
    if not text:
        raise ProviderError(NAME, "empty symbol", retryable=False)
    base = text
    suffix: str | None = None
    for yahoo_suffix in sorted(_YAHOO_SUFFIX_TO_STOOQ, key=len, reverse=True):
        if yahoo_suffix and text.endswith(yahoo_suffix):
            base = text[: -len(yahoo_suffix)]
            suffix = _YAHOO_SUFFIX_TO_STOOQ[yahoo_suffix]
            break
    if suffix is None:
        try:
            key = (market or "").strip().upper()
        except Exception:
            key = ""
        suffix = _MIC_TO_STOOQ.get(key, ".us")
    base = base.strip().strip(".")
    if not base:
        raise ProviderError(NAME, "empty symbol", retryable=False)
    return f"{base.lower()}{suffix}"


def currency_for_stooq(stooq_symbol: str, fallback: str = "USD") -> str:
    """Quote currency from the Stooq suffix (``aapl.us`` -> ``USD``)."""
    lower = (stooq_symbol or "").strip().lower()
    for stooq_suffix, ccy in _STOOQ_SUFFIX_TO_CCY.items():
        if lower.endswith(stooq_suffix):
            return ccy
    return fallback


def _coerce_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    text = str(value).strip()
    if not text or text.upper() == "N/D":
        return None
    try:
        number = float(text)
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


def parse_stooq_csv(text: str, *, symbol: str, stooq_symbol: str) -> dict:
    """Parse one Stooq CSV snapshot row into a raw quote dict.

    Raises ProviderError when the table is empty or the row is ``N/D``
    (unknown symbol / no data). Never fabricates ``prev_close``.
    """
    try:
        reader = csv.DictReader(io.StringIO(text or ""))
        rows = [row for row in reader if row]
    except Exception as exc:
        raise ProviderError(NAME, f"{type(exc).__name__}: {exc}") from exc
    if not rows:
        raise ProviderError(NAME, f"no data for {symbol}")
    row = rows[0]
    # Header is Symbol,Date,Time,Open,High,Low,Close,Volume (case varies).
    lowered = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
    close = _coerce_float(lowered.get("close"))
    if close is None:
        raise ProviderError(NAME, f"no data for {symbol}")
    return {
        "symbol": (symbol or "").strip().upper(),
        "price": close,
        "open": _coerce_float(lowered.get("open")),
        "high": _coerce_float(lowered.get("high")),
        "low": _coerce_float(lowered.get("low")),
        "prev_close": None,  # single snapshot: never fabricate
        "volume": _coerce_int(lowered.get("volume")),
        "currency": currency_for_stooq(stooq_symbol),
        "as_of": _utcnow(),
    }


class StooqProvider:
    """Live Stooq provider (no key, ~15min delayed) with stub fallback."""

    name = NAME

    def __init__(
        self,
        *,
        breaker: CircuitBreaker | None = None,
        limiter: RateLimiter | None = None,
        delay_minutes: int = DEFAULT_DELAY_MINUTES,
        stub_mode: bool = False,
        on_call: object | None = None,
        timeout_s: float = 12.0,
    ) -> None:
        self.breaker = breaker or CircuitBreaker()
        # Free endpoint: be gentle (2 rps, burst 4).
        self.limiter = limiter or RateLimiter(rate_per_sec=2.0, burst=4)
        self.delay_minutes = delay_minutes
        self.stub_mode = stub_mode
        self.timeout_s = timeout_s
        self._on_call = on_call  # health hook: fn(provider, latency_ms, ok)

    # -- internals ------------------------------------------------------
    def _emit(self, latency_ms: float, ok: bool, *, error=None, status_code=None) -> None:
        from .base import emit_health as _emit_health

        _emit_health(self._on_call, self.name, latency_ms, ok,
                     error=error, status_code=status_code)

    def _stub_quote(self, symbol: str) -> dict:
        from ..normalization import normalize_quote  # local import: no cycle

        upper = (symbol or "").strip().upper()
        base = dict(STUB_QUOTES.get(upper, {"price": 100.0, "currency": "USD"}))
        if "currency" not in base:
            base["currency"] = "USD"
        base.update({"symbol": upper, "as_of": _utcnow(), "fallback": True})
        quote = normalize_quote(base, source=self.name, delay_minutes=self.delay_minutes)
        quote["fallback_used"] = True
        return quote

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_fixed(1),
        retry=retry_if_exception_type(ProviderError),
        reraise=True,
    )
    def _fetch_raw(self, symbol: str, market: str | None = None) -> dict:
        try:
            import httpx  # lazy: offline/test envs fall back to stub
        except Exception as exc:
            raise ProviderError(NAME, "httpx package unavailable") from exc
        upper = (symbol or "").strip().upper()
        if not upper:
            raise ProviderError(NAME, "empty symbol", retryable=False)
        stooq_symbol = to_stooq_symbol(upper, market)
        url = f"{BASE_URL}?s={stooq_symbol}&f=sd2t2ohlcv&h&e=csv"
        try:
            resp = httpx.get(url, timeout=self.timeout_s, follow_redirects=True)
        except Exception as exc:  # network / timeout
            raise ProviderError(NAME, f"{type(exc).__name__}: {exc}") from exc
        try:
            status = int(getattr(resp, "status_code", 200) or 200)
        except (TypeError, ValueError):
            status = 200
        if status == 429:
            raise ProviderError(NAME, "rate limited (429)")
        if status >= 400:
            raise ProviderError(NAME, f"HTTP {status}")
        try:
            text = resp.text
        except Exception as exc:
            raise ProviderError(NAME, f"{type(exc).__name__}: {exc}") from exc
        # Stooq sometimes answers 200 with an empty body on abuse; treat as
        # transient (retry once) rather than "unknown symbol".
        if not (text or "").strip():
            raise ProviderError(NAME, "empty response")
        # Stooq enforces an undisclosed daily request quota per key/IP and
        # reports it as HTTP 200 with a plain-text/HTML body (never 429),
        # e.g. "Exceeded the daily hits limit". Detect the body explicitly
        # so quota exhaustion reads as rate-limited (retryable, breaker +
        # health aware) instead of masquerading as "unknown symbol".
        if "exceeded the daily hits limit" in (text or "").lower():
            raise ProviderError(NAME, "rate limited (daily quota)")
        # Preserve the Yahoo-style symbol for registry parity, not stooq's.
        raw = parse_stooq_csv(text, symbol=upper, stooq_symbol=stooq_symbol)
        # Euronext/US currency guard: suffix-derived above; keep as parsed.
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
        quote = normalize_quote(raw, source=self.name, delay_minutes=self.delay_minutes)
        quote["fallback_used"] = False
        return quote
