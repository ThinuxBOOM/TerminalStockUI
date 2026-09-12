"""yfinance provider (default free source for NYSE/NASDAQ/SSE/Euronext).

Resilience: per-provider circuit breaker, token-bucket rate-limit stub,
tenacity retry on transient failures, deterministic offline stub fallback
(flagged fallback_used=True, grade C) so the API stays usable on outage.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

from .base import CircuitBreaker, ProviderError, RateLimiter

try:  # optional at import time; stub fallback covers offline/test envs
    import yfinance as yf
except Exception:  # pragma: no cover
    yf = None  # type: ignore[assignment]

NAME = "yfinance"
DEFAULT_DELAY_MINUTES = 15

#: Deterministic offline reference quotes (used only as outage fallback /
#: forced-stub mode; always served with fallback_used=True).
STUB_QUOTES: dict[str, dict] = {
    "AAPL": {"price": 232.50, "open": 231.00, "high": 233.80, "low": 230.10,
             "prev_close": 230.75, "volume": 54_000_000, "currency": "USD"},
    "MSFT": {"price": 428.15, "open": 426.00, "high": 429.90, "low": 425.20,
             "prev_close": 425.60, "volume": 21_000_000, "currency": "USD"},
    "NVDA": {"price": 131.88, "open": 130.50, "high": 132.70, "low": 129.90,
             "prev_close": 129.40, "volume": 240_000_000, "currency": "USD"},
    "AAP": {"price": 62.40, "open": 61.90, "high": 63.10, "low": 61.50,
            "prev_close": 61.80, "volume": 1_800_000, "currency": "USD"},
    "JPM": {"price": 224.30, "open": 223.10, "high": 225.00, "low": 222.40,
            "prev_close": 222.90, "volume": 9_500_000, "currency": "USD"},
    "600519.SS": {"price": 1680.00, "open": 1672.00, "high": 1688.00, "low": 1665.00,
                  "prev_close": 1670.50, "volume": 3_200_000, "currency": "CNY"},
    "600000.SS": {"price": 10.85, "open": 10.78, "high": 10.92, "low": 10.74,
                  "prev_close": 10.76, "volume": 120_000_000, "currency": "CNY"},
    "MC.PA": {"price": 715.50, "open": 712.00, "high": 718.20, "low": 710.40,
              "prev_close": 711.30, "volume": 480_000, "currency": "EUR"},
    "ACA.PA": {"price": 14.62, "open": 14.55, "high": 14.70, "low": 14.50,
               "prev_close": 14.52, "volume": 8_900_000, "currency": "EUR"},
    "ASML.AS": {"price": 812.30, "open": 808.00, "high": 815.60, "low": 805.10,
                "prev_close": 806.90, "volume": 620_000, "currency": "EUR"},
    "UCB.BR": {"price": 168.40, "open": 167.20, "high": 169.10, "low": 166.80,
               "prev_close": 166.95, "volume": 310_000, "currency": "EUR"},
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class YFinanceProvider:
    """Live yfinance provider with stub fallback."""

    name = NAME

    def __init__(
        self,
        *,
        breaker: CircuitBreaker | None = None,
        limiter: RateLimiter | None = None,
        delay_minutes: int = DEFAULT_DELAY_MINUTES,
        stub_mode: bool = False,
        on_call: object | None = None,
    ) -> None:
        self.breaker = breaker or CircuitBreaker()
        self.limiter = limiter or RateLimiter()
        self.delay_minutes = delay_minutes
        self.stub_mode = stub_mode
        self._on_call = on_call  # health hook: fn(provider, latency_ms, ok)

    # -- internals ------------------------------------------------------
    def _emit(self, latency_ms: float, ok: bool) -> None:
        if self._on_call is not None:
            try:
                self._on_call(self.name, latency_ms, ok)  # type: ignore[misc]
            except Exception:
                pass

    def _stub_quote(self, symbol: str) -> dict:
        from ..normalization import normalize_quote  # local import: no cycle

        upper = symbol.upper()
        base = dict(STUB_QUOTES.get(upper, {"price": 100.0, "currency": "USD"}))
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
    def _fetch_raw(self, symbol: str) -> dict:
        if yf is None:
            raise ProviderError(self.name, "yfinance package unavailable")
        try:
            ticker = yf.Ticker(symbol)
            try:
                info = ticker.fast_info  # type: ignore[attr-defined]
                price = getattr(info, "last_price", None)
            except Exception:
                price = None
            hist = ticker.history(period="2d", auto_adjust=True)
            if hist is None or len(hist) == 0:
                if price is None:
                    raise ProviderError(self.name, f"no data for {symbol}")
                return {"symbol": symbol.upper(), "price": float(price),
                        "currency": "USD", "as_of": _utcnow()}
            last = hist.iloc[-1]
            prev = hist.iloc[-2] if len(hist) > 1 else last
            currency = getattr(ticker, "fast_info", None)
            ccy = "USD"
            try:
                ccy = ticker.fast_info.currency or "USD"  # type: ignore[attr-defined]
            except Exception:
                pass
            _ = currency
            return {
                "symbol": symbol.upper(),
                "price": float(last["Close"]),
                "open": float(last["Open"]),
                "high": float(last["High"]),
                "low": float(last["Low"]),
                "prev_close": float(prev["Close"]),
                "volume": int(last["Volume"]) if last["Volume"] == last["Volume"] else None,
                "currency": ccy,
                "as_of": _utcnow(),
            }
        except ProviderError:
            raise
        except Exception as exc:  # network / parse failure
            raise ProviderError(self.name, f"{type(exc).__name__}: {exc}") from exc

    # -- public ---------------------------------------------------------
    def get_quote(self, symbol: str) -> dict:
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
            self._emit(0.0, False)
            return quote

        self.limiter.acquire()  # stub: counted, never blocks local run
        started = time.perf_counter()
        try:
            raw = self._fetch_raw(upper)
        except ProviderError:
            self.breaker.record_failure()
            self._emit((time.perf_counter() - started) * 1000, False)
            quote = self._stub_quote(upper)
            quote["circuit_open"] = self.breaker.state != CircuitBreaker.CLOSED
            return quote
        self.breaker.record_success()
        self._emit((time.perf_counter() - started) * 1000, True)
        quote = normalize_quote(raw, source=self.name, delay_minutes=self.delay_minutes)
        quote["fallback_used"] = False
        return quote
