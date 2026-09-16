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
    "TSLA": {"price": 248.50, "open": 246.00, "high": 250.20, "low": 244.80,
             "prev_close": 245.10, "volume": 98_000_000, "currency": "USD"},
    "GOOGL": {"price": 176.30, "open": 175.00, "high": 177.10, "low": 174.20,
              "prev_close": 174.80, "volume": 24_000_000, "currency": "USD"},
    "AMZN": {"price": 186.40, "open": 185.00, "high": 187.20, "low": 184.10,
             "prev_close": 184.90, "volume": 38_000_000, "currency": "USD"},
    "META": {"price": 563.20, "open": 560.00, "high": 565.40, "low": 558.10,
             "prev_close": 559.30, "volume": 11_000_000, "currency": "USD"},
    "AVGO": {"price": 172.80, "open": 171.00, "high": 173.60, "low": 170.20,
             "prev_close": 170.90, "volume": 18_000_000, "currency": "USD"},
    "SPY": {"price": 542.10, "open": 540.00, "high": 543.20, "low": 539.10,
            "prev_close": 539.80, "volume": 55_000_000, "currency": "USD"},
    "QQQ": {"price": 478.60, "open": 476.00, "high": 479.80, "low": 475.20,
            "prev_close": 475.90, "volume": 32_000_000, "currency": "USD"},
    "EWQ": {"price": 38.20, "open": 38.00, "high": 38.40, "low": 37.80,
            "prev_close": 37.95, "volume": 280_000, "currency": "USD"},
    "EWN": {"price": 27.40, "open": 27.20, "high": 27.55, "low": 27.05,
            "prev_close": 27.15, "volume": 190_000, "currency": "USD"},
    "EWK": {"price": 19.80, "open": 19.65, "high": 19.95, "low": 19.50,
            "prev_close": 19.60, "volume": 95_000, "currency": "USD"},
    "AAP": {"price": 62.40, "open": 61.90, "high": 63.10, "low": 61.50,
            "prev_close": 61.80, "volume": 1_800_000, "currency": "USD"},
    "JPM": {"price": 224.30, "open": 223.10, "high": 225.00, "low": 222.40,
            "prev_close": 222.90, "volume": 9_500_000, "currency": "USD"},
    "600519.SS": {"price": 1680.00, "open": 1672.00, "high": 1688.00, "low": 1665.00,
                  "prev_close": 1670.50, "volume": 3_200_000, "currency": "CNY"},
    "600000.SS": {"price": 10.85, "open": 10.78, "high": 10.92, "low": 10.74,
                  "prev_close": 10.76, "volume": 120_000_000, "currency": "CNY"},
    "000001.SS": {"price": 3380.00, "open": 3370.00, "high": 3395.00, "low": 3362.00,
                  "prev_close": 3368.50, "volume": 280_000_000, "currency": "CNY"},
    "MC.PA": {"price": 715.50, "open": 712.00, "high": 718.20, "low": 710.40,
              "prev_close": 711.30, "volume": 480_000, "currency": "EUR"},
    "ACA.PA": {"price": 14.62, "open": 14.55, "high": 14.70, "low": 14.50,
               "prev_close": 14.52, "volume": 8_900_000, "currency": "EUR"},
    "CAC.PA": {"price": 82.40, "open": 82.00, "high": 82.80, "low": 81.70,
               "prev_close": 81.90, "volume": 4_200_000, "currency": "EUR"},
    "ASML.AS": {"price": 812.30, "open": 808.00, "high": 815.60, "low": 805.10,
                "prev_close": 806.90, "volume": 620_000, "currency": "EUR"},
    "IAEX.AS": {"price": 148.20, "open": 147.50, "high": 148.80, "low": 147.00,
                "prev_close": 147.30, "volume": 310_000, "currency": "EUR"},
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
        timeout_s: float = 10.0,
    ) -> None:
        self.breaker = breaker or CircuitBreaker()
        self.limiter = limiter or RateLimiter()
        self.delay_minutes = delay_minutes
        self.stub_mode = stub_mode
        self._on_call = on_call  # health hook: fn(provider, latency_ms, ok)
        try:
            self.timeout_s = max(3.0, min(float(timeout_s), 25.0))
        except (TypeError, ValueError):
            self.timeout_s = 10.0

    # -- internals ------------------------------------------------------
    def _emit(self, latency_ms: float, ok: bool, *, error=None, status_code=None) -> None:
        from .base import emit_health as _emit_health

        _emit_health(self._on_call, self.name, latency_ms, ok,
                     error=error, status_code=status_code)

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
            from concurrent.futures import ThreadPoolExecutor as _TPE

            def _do_fetch():
                ticker = yf.Ticker(symbol)
                try:
                    info = ticker.fast_info  # type: ignore[attr-defined]
                    price = getattr(info, "last_price", None)
                except Exception:
                    price = None
                try:
                    hist = ticker.history(period="2d", auto_adjust=True, timeout=8)
                except TypeError:
                    hist = ticker.history(period="2d", auto_adjust=True)
                return price, ticker, hist

            with _TPE(max_workers=1) as _pool:
                price, ticker, hist = _pool.submit(_do_fetch).result(timeout=self.timeout_s)
            def _safe_volume(value: object) -> int | None:
                # None/inf/str must not kill a live quote (int(None) raises).
                try:
                    number = float(value)  # type: ignore[arg-type]
                except (TypeError, ValueError, OverflowError):
                    return None
                if number != number or number in (float("inf"), float("-inf")):
                    return None
                try:
                    return int(number)
                except (TypeError, ValueError, OverflowError):
                    return None

            def _safe_price(value: object) -> float | None:
                # OHLC holes must degrade to missing fields, never kill the
                # whole live quote (float(None) raises TypeError).
                try:
                    number = float(value)  # type: ignore[arg-type]
                except (TypeError, ValueError, OverflowError):
                    return None
                if number != number or number in (float("inf"), float("-inf")):
                    return None
                return number

            # Yahoo appends today's still-forming bar with NaN OHLC (volume
            # only) while the session develops or before it finalizes. The
            # latest row is therefore NOT always usable: scan back for the
            # last COMPLETE row (finite close) instead of dying on NaNs.
            # (2026-09-15: 600519.SS served NaN-today + Sept-14-complete;
            # blindly taking iloc[-1] 502'd every SSE quote.)
            last = None
            prev = None
            if hist is not None and len(hist) > 0:
                for back in range(len(hist)):
                    idx = len(hist) - 1 - back
                    try:
                        candidate = hist.iloc[idx]
                    except (IndexError, KeyError):
                        break
                    try:
                        if _safe_price(candidate["Close"]) is not None:
                            last = candidate
                            prev = hist.iloc[idx - 1] if idx - 1 >= 0 else None
                            break
                    except (KeyError, IndexError, TypeError, ValueError):
                        continue
            if last is None:
                if price is None:
                    raise ProviderError(self.name, f"no data for {symbol}")
                # History empty but fast_info has a price: keep its currency
                # (hardcoding USD here once mislabeled Euronext quotes).
                ccy_only = "USD"
                try:
                    ccy_only = ticker.fast_info.currency or "USD"  # type: ignore[attr-defined]
                except Exception:
                    pass
                return {"symbol": symbol.upper(), "price": float(price),
                        "currency": ccy_only, "as_of": _utcnow()}
            # Complete row found above; single-row history has no observable
            # prior close (prev stays None: never fabricate change=0).
            currency = getattr(ticker, "fast_info", None)
            ccy = "USD"
            try:
                ccy = ticker.fast_info.currency or "USD"  # type: ignore[attr-defined]
            except Exception:
                pass
            _ = currency
            close = _safe_price(last["Close"])
            if close is None:
                raise ProviderError(self.name, f"no data for {symbol}")
            return {
                "symbol": symbol.upper(),
                "price": close,
                "open": _safe_price(last["Open"]),
                "high": _safe_price(last["High"]),
                "low": _safe_price(last["Low"]),
                "prev_close": _safe_price(prev["Close"]) if prev is not None else None,
                "volume": _safe_volume(last["Volume"]),
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
            self._emit(0.0, False, error="circuit open (breaker)")
            return quote

        self.limiter.acquire()  # stub: counted, never blocks local run
        started = time.perf_counter()
        try:
            raw = self._fetch_raw(upper)
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
