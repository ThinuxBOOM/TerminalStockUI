"""AKShare provider (M6 SSE expansion, fallback for XSHG).

Free-first SSE source complementing yfinance (.SS). Optional dependency:
``akshare`` is never imported at module load in a crashing way -- lazy import
with graceful stub fallback (flagged ``fallback_used=True``).

Symbol handling:
  - Accepts ``600519.SS`` or bare ``600519`` (+ market=XSHG).
  - Strips ``.SS`` to a 6-digit AKShare code for upstream calls.
  - Returns canonical Yahoo-style symbol ``600519.SS`` for registry parity.

Currency is always CNY for XSHG (never USD).

Timezone:
  - ``as_of`` is stored as UTC ISO (datetime with tzinfo=UTC).
  - Display layer converts to Asia/Shanghai via :func:`shanghai_display`.

Never fabricates live prices: live path uses AKShare only; stub is served
exclusively as flagged fallback when akshare is missing or network fails.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

from backend.market_data.providers.base import CircuitBreaker, ProviderError, RateLimiter

try:  # optional dep; stub fallback covers offline/test envs
    import akshare as ak  # type: ignore[import-not-found]
except Exception:  # pragma: no cover
    ak = None  # type: ignore[assignment]

NAME = "akshare"
DEFAULT_DELAY_MINUTES = 15
SHANGHAI_TZ = "Asia/Shanghai"

#: Deterministic offline reference quotes (outage fallback / forced-stub mode
#: only; always served with fallback_used=True, never as live prices).
STUB_QUOTES: dict[str, dict] = {
    "600519.SS": {"price": 1680.00, "open": 1672.00, "high": 1688.00, "low": 1665.00,
                  "prev_close": 1670.50, "volume": 3_200_000, "currency": "CNY"},
    "600000.SS": {"price": 10.85, "open": 10.78, "high": 10.92, "low": 10.74,
                  "prev_close": 10.76, "volume": 120_000_000, "currency": "CNY"},
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def shanghai_display(as_of_utc: datetime) -> str:
    """Format a UTC ``as_of`` for Asia/Shanghai display (store UTC ISO)."""
    if as_of_utc.tzinfo is None:
        as_of_utc = as_of_utc.replace(tzinfo=timezone.utc)
    try:
        local = as_of_utc.astimezone(ZoneInfo(SHANGHAI_TZ))
    except Exception:
        return as_of_utc.isoformat()
    return local.isoformat()


def to_akshare_code(symbol: str, market: str | None = None) -> str:
    """Normalize ``600519.SS`` / ``600519`` (+ market=XSHG) to ``600519``.

    Strips a trailing ``.SS`` (case-insensitive). Raises ProviderError on
    empty input (retryable=False).
    """
    _ = market  # accepted for call-site parity; code is suffix-derived
    text = (symbol or "").strip().upper()
    if not text:
        raise ProviderError(NAME, "empty symbol", retryable=False)
    if text.endswith(".SS"):
        text = text[: -len(".SS")]
    return text.strip()


def to_yahoo_symbol(symbol_or_code: str) -> str:
    """Canonical Yahoo-style symbol for an SSE code (``600519`` -> ``600519.SS``)."""
    text = (symbol_or_code or "").strip().upper()
    if not text:
        return text
    if text.endswith(".SS"):
        return text
    # Bare 6-digit SSE codes get the .SS suffix; anything else passes through.
    if text.isdigit() and len(text) == 6:
        return f"{text}.SS"
    return text


def _coerce_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        if isinstance(value, bool):
            return float(value)
        f = float(value)  # type: ignore[arg-type]
        if f != f:  # NaN
            return None
        import math as _math

        if not _math.isfinite(f):  # inf -> None (JSON-safe, never leaks)
            return None
        return f
    except (TypeError, ValueError, OverflowError):
        return None


def _coerce_int(value: object) -> int | None:
    try:
        if value is None:
            return None
        if isinstance(value, bool):
            return int(value)
        f = float(value)  # type: ignore[arg-type]
        if f != f:
            return None
        import math as _math

        if not _math.isfinite(f):
            return None
        return int(f)
    except (TypeError, ValueError, OverflowError):
        return None


class AKShareProvider:
    """Live AKShare provider with stub fallback (CNY, XSHG)."""

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

    def _stub_quote(self, symbol: str, market: str | None = None) -> dict:
        from backend.market_data.normalization import normalize_quote  # local: no cycle

        upper = (symbol or "").strip().upper()
        if not upper:
            upper = "600519.SS"
        canonical = to_yahoo_symbol(to_akshare_code(upper, market) or upper)
        if not canonical:
            canonical = upper
        base = dict(STUB_QUOTES.get(canonical, {"price": 100.0, "currency": "CNY"}))
        # Never serve USD from the SSE provider; force CNY.
        base["currency"] = "CNY"
        base.update({"symbol": canonical, "as_of": _utcnow(), "fallback": True})
        quote = normalize_quote(base, source=self.name, delay_minutes=self.delay_minutes)
        # Enforce CNY post-normalization (normalize preserves raw currency).
        quote["currency"] = "CNY"
        if "currency" in quote.get("missing_fields", []):
            quote["missing_fields"] = [f for f in quote["missing_fields"] if f != "currency"]
        quote["fallback_used"] = True
        return quote

    def _fetch_spot(self, code: str) -> dict:
        """Fetch one SSE quote via ``stock_zh_a_spot_em`` (Chinese columns)."""
        assert ak is not None
        fetch = getattr(ak, "stock_zh_a_spot_em", None)
        if fetch is None:
            raise ProviderError(self.name, "akshare spot API unavailable")
        df = fetch()
        if df is None or len(df) == 0:
            raise ProviderError(self.name, f"no data for {code}")
        # Column is Chinese "代码"; be defensive about missing columns.
        try:
            if "代码" in getattr(df, "columns", []):
                rows = df[df["代码"].astype(str) == str(code)]
                if len(rows) == 0:
                    raise ProviderError(self.name, f"no data for {code}")
                row = rows.iloc[0].to_dict()
            else:  # unexpected schema
                raise ProviderError(self.name, "akshare spot schema mismatch")
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(self.name, f"{type(exc).__name__}: {exc}") from exc
        price = _coerce_float(row.get("最新价"))
        if price is None:
            raise ProviderError(self.name, f"no data for {code}")
        return {
            "symbol": to_yahoo_symbol(code),
            "price": price,
            "open": _coerce_float(row.get("今开")),
            "high": _coerce_float(row.get("最高")),
            "low": _coerce_float(row.get("最低")),
            "prev_close": _coerce_float(row.get("昨收")),
            "volume": _coerce_int(row.get("成交量")),
            "currency": "CNY",
            "as_of": _utcnow(),
        }

    def _fetch_hist(self, code: str) -> dict:
        """Fallback fetch via ``stock_zh_a_hist`` daily bars (last two rows)."""
        assert ak is not None
        fetch = getattr(ak, "stock_zh_a_hist", None)
        if fetch is None:
            raise ProviderError(self.name, "akshare hist API unavailable")
        try:
            df = fetch(symbol=str(code), period="daily", adjust="qfq")
        except TypeError:
            # Older signatures: stock_zh_a_hist(symbol=code)
            df = fetch(symbol=str(code))
        if df is None or len(df) == 0:
            raise ProviderError(self.name, f"no data for {code}")
        try:
            last = df.iloc[-1].to_dict()
            # Single-row history has no observable prior close: leave
            # prev_close missing (never fabricate change=0 "flat day").
            prev = df.iloc[-2].to_dict() if len(df) > 1 else None
        except Exception as exc:
            raise ProviderError(self.name, f"{type(exc).__name__}: {exc}") from exc
        # Hist columns vary by version; try Chinese then English aliases.
        def _pick(d: dict, *keys: str) -> object:
            for k in keys:
                if d.get(k) is not None:
                    return d.get(k)
            return None

        price = _coerce_float(_pick(last, "收盘", "close", "最新价"))
        if price is None:
            raise ProviderError(self.name, f"no data for {code}")
        prev_close = _coerce_float(_pick(prev, "收盘", "close", "昨收")) if prev else None
        return {
            "symbol": to_yahoo_symbol(code),
            "price": price,
            "open": _coerce_float(_pick(last, "开盘", "open", "今开")),
            "high": _coerce_float(_pick(last, "最高", "high")),
            "low": _coerce_float(_pick(last, "最低", "low")),
            "prev_close": prev_close,
            "volume": _coerce_int(_pick(last, "成交量", "volume")),
            "currency": "CNY",
            "as_of": _utcnow(),
        }

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_fixed(1),
        retry=retry_if_exception_type(ProviderError),
        reraise=True,
    )
    def _fetch_raw(self, symbol: str, market: str | None = None) -> dict:
        if ak is None:
            raise ProviderError(self.name, "akshare package unavailable")
        code = to_akshare_code(symbol, market)
        if not code:
            raise ProviderError(self.name, "empty symbol", retryable=False)
        last_error: ProviderError | None = None
        try:
            return self._fetch_spot(code)
        except ProviderError as exc:
            last_error = exc
        except Exception as exc:  # defensive: unexpected failure
            last_error = ProviderError(self.name, f"{type(exc).__name__}: {exc}")
        # Spot failed -> try daily history before giving up.
        try:
            return self._fetch_hist(code)
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(self.name, f"{type(exc).__name__}: {exc}") from exc
        # Unreachable: _fetch_hist either returns or raises; keep for safety.
        if last_error is not None:  # pragma: no cover
            raise last_error
        raise ProviderError(self.name, f"no data for {code}")

    # -- public ---------------------------------------------------------
    def get_quote(self, symbol: str, market: str | None = None) -> dict:
        from backend.market_data.normalization import normalize_quote

        upper = (symbol or "").strip().upper()
        if not upper:
            raise ProviderError(self.name, "empty symbol", retryable=False)

        if self.stub_mode:
            quote = self._stub_quote(upper, market)
            self._emit(0.0, True)
            return quote

        if not self.breaker.allow_request():
            quote = self._stub_quote(upper, market)
            quote["circuit_open"] = True
            self._emit(0.0, False)
            return quote

        self.limiter.acquire()  # stub: counted, never blocks local run
        started = time.perf_counter()
        try:
            raw = self._fetch_raw(upper, market)
        except ProviderError:
            self.breaker.record_failure()
            self._emit((time.perf_counter() - started) * 1000, False)
            quote = self._stub_quote(upper, market)
            quote["circuit_open"] = self.breaker.state != CircuitBreaker.CLOSED
            return quote
        self.breaker.record_success()
        self._emit((time.perf_counter() - started) * 1000, True)
        # Never serve USD from SSE provider.
        raw["currency"] = "CNY"
        quote = normalize_quote(raw, source=self.name, delay_minutes=self.delay_minutes)
        quote["currency"] = "CNY"
        if "currency" in quote.get("missing_fields", []):
            quote["missing_fields"] = [f for f in quote["missing_fields"] if f != "currency"]
        quote["fallback_used"] = False
        return quote
