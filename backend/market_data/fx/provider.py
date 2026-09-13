"""FX provider (M7 cross-market gate): free live sources with provenance.

Free-first design mirroring the equity providers:

- live path 1: ``https://api.frankfurter.app/latest?from=BASE&to=QUOTE``
  (ECB reference rates), ``httpx`` imported lazily inside the fetch so the
  module stays import-safe offline and ``httpx`` is strictly optional;
- live path 2 (secondary): yfinance FX tickers (``EURUSD=X`` …,
  inverse ``USDCNY=X`` inverted when the direct pair is missing) — the same
  network path as the equity quotes, so environments that can fetch quotes
  can fetch FX. ``yfinance`` imported lazily; missing/unusable -> stub;
- 15-minute in-memory cache of live rates (``CACHE_TTL_S``);
- fallback: deterministic ECB reference stub table (triangle-consistent
  ``EURUSD 1.08 / USDCNY 7.25 / EURCNY 7.83``), always flagged
  ``fallback_used=True``;
- per-provider circuit breaker + token-bucket limiter, health hook, and a
  ``stub_mode`` force-offline switch — like the yfinance/AKShare providers;
- never raises on network failure: any outage degrades to the flagged stub.
  Only empty/unsupported currency codes raise :class:`ProviderError`
  (``retryable=False``), matching the equity empty-symbol contract.

``get_rate(base, quote)`` returns ``{pair, base, quote, rate, inverse, as_of,
source, delay_minutes, missing_fields, fallback_used}``. Use
:meth:`FXProvider.provenance_for` to build the standard provenance envelope.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

from backend.market_data.providers.base import CircuitBreaker, ProviderError, RateLimiter
from backend.market_data.provenance import Provenance, build_provenance
from backend.market_data.quality import grade_quality

NAME = "fx"
LIVE_SOURCE = "frankfurter"
YF_SOURCE = "yfinance"
STUB_SOURCE = "fx"  # local ECB reference table served by this provider
BASE_URL = "https://api.frankfurter.app"
DEFAULT_DELAY_MINUTES = 15
CACHE_TTL_S = 15 * 60

SUPPORTED_CURRENCIES = ("USD", "EUR", "CNY")

#: Deterministic ECB reference stub, quoted as units per EUR
#: (1 EUR = X CCY). Triangle-consistent: 1.08 * 7.25 == 7.83.
PER_EUR_STUB: dict[str, float] = {"EUR": 1.0, "USD": 1.08, "CNY": 7.83}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _check_ccy(code: str) -> str:
    text = (code or "").strip().upper()
    if not text:
        raise ProviderError(NAME, "empty currency", retryable=False)
    if text not in SUPPORTED_CURRENCIES:
        raise ProviderError(NAME, f"unsupported currency {text}", retryable=False)
    return text


def stub_rate(base: str, quote: str) -> float:
    """Deterministic ECB reference stub rate for ``base`` -> ``quote``."""
    return PER_EUR_STUB[quote] / PER_EUR_STUB[base]


class FXProvider:
    """Live Frankfurter/ECB provider with yfinance secondary + stub fallback.

    Chain per pair: frankfurter -> yfinance FX ticker -> flagged stub.
    Never crashes offline."""

    def _fetch_yahoo(self, base: str, quote: str) -> dict:
        """Secondary live fetch via yfinance FX tickers (``EURUSD=X`` …).

        Tries the direct pair first, then the inverse pair (inverted).
        Raises :class:`ProviderError` when yfinance is unavailable or both
        tickers yield no usable close — the caller then serves the stub.
        """
        try:  # lazy: never imported at module load; offline envs stay safe
            import yfinance as yf
        except Exception as exc:
            raise ProviderError(NAME, "yfinance package unavailable") from exc
        last_exc: Exception | None = None
        for symbol, invert in (
            (f"{base}{quote}=X", False),
            (f"{quote}{base}=X", True),
        ):
            try:
                hist = yf.Ticker(symbol).history(period="2d", auto_adjust=True)
                if hist is None or len(hist) == 0:
                    raise ProviderError(NAME, f"no data for {symbol}")
                close = float(hist["Close"].iloc[-1])
                if not (close == close and close > 0):
                    raise ProviderError(NAME, f"bad close for {symbol}")
            except ProviderError as exc:
                last_exc = exc
                continue
            except Exception as exc:  # network / parse failure
                last_exc = exc
                continue
            rate = close if not invert else 1.0 / close
            return {
                "base": base,
                "quote": quote,
                "rate": rate,
                "as_of": _utcnow(),
                "source": YF_SOURCE,
            }
        raise ProviderError(
            NAME, f"yahoo FX unavailable for {base}/{quote}: {last_exc}"
        ) from last_exc

    name = NAME

    def __init__(
        self,
        *,
        breaker: CircuitBreaker | None = None,
        limiter: RateLimiter | None = None,
        delay_minutes: int = DEFAULT_DELAY_MINUTES,
        cache_ttl_s: int = CACHE_TTL_S,
        stub_mode: bool = False,
        on_call: object | None = None,
    ) -> None:
        self.breaker = breaker or CircuitBreaker()
        self.limiter = limiter or RateLimiter()
        self.delay_minutes = delay_minutes
        self.cache_ttl_s = cache_ttl_s
        self.stub_mode = stub_mode
        self._on_call = on_call  # health hook: fn(provider, latency_ms, ok)
        self._cache: dict[str, tuple[float, dict]] = {}

    # -- internals ------------------------------------------------------
    def _emit(self, latency_ms: float, ok: bool) -> None:
        if self._on_call is not None:
            try:
                self._on_call(self.name, latency_ms, ok)  # type: ignore[misc]
            except Exception:
                pass

    def _cache_get(self, key: str) -> dict | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        expires_at, payload = entry
        if time.monotonic() >= expires_at:
            self._cache.pop(key, None)
            return None
        return dict(payload)

    def _cache_put(self, key: str, payload: dict) -> None:
        self._cache[key] = (time.monotonic() + self.cache_ttl_s, dict(payload))

    def _stub_payload(self, base: str, quote: str) -> dict:
        rate = stub_rate(base, quote)
        return {
            "pair": f"{base}/{quote}",
            "base": base,
            "quote": quote,
            "rate": rate,
            "inverse": 1.0 / rate,
            "as_of": _utcnow(),
            "source": STUB_SOURCE,
            "delay_minutes": self.delay_minutes,
            "missing_fields": [],
            "fallback_used": True,
        }

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_fixed(1),
        retry=retry_if_exception_type(ProviderError),
        reraise=True,
    )
    def _fetch_raw(self, base: str, quote: str) -> dict:
        try:  # lazy: never imported at module load; offline envs stay import-safe
            import httpx  # type: ignore[import-not-found]
        except Exception as exc:
            raise ProviderError(NAME, "httpx package unavailable") from exc
        try:
            resp = httpx.get(
                f"{BASE_URL}/latest", params={"from": base, "to": quote}, timeout=5.0
            )
            resp.raise_for_status()
            data = resp.json()
        except ProviderError:
            raise
        except Exception as exc:  # network / HTTP / decode failure
            raise ProviderError(NAME, f"{type(exc).__name__}: {exc}") from exc
        try:
            rate = float((data.get("rates") or {})[quote])
            if rate <= 0:
                raise ValueError("non-positive rate")
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError(NAME, f"unexpected frankfurter schema: {exc}") from exc
        return {
            "base": base,
            "quote": quote,
            "rate": rate,
            "as_of": _utcnow(),
            "source": LIVE_SOURCE,
        }

    # -- public ---------------------------------------------------------
    def get_rate(self, base: str, quote: str) -> dict:
        """Return ``{pair, rate, inverse, as_of, source, fallback_used, ...}``.

        Never raises on outage (flagged stub); raises :class:`ProviderError`
        (``retryable=False``) only for empty/unsupported currency codes.
        """
        b = _check_ccy(base)
        q = _check_ccy(quote)
        if b == q:  # identity needs no upstream
            return {
                "pair": f"{b}/{q}",
                "base": b,
                "quote": q,
                "rate": 1.0,
                "inverse": 1.0,
                "as_of": _utcnow(),
                "source": self.name,
                "delay_minutes": self.delay_minutes,
                "missing_fields": [],
                "fallback_used": False,
            }

        cache_key = f"{b}/{q}"
        if not self.stub_mode:
            hit = self._cache_get(cache_key)
            if hit is not None:
                self._emit(0.0, True)
                return hit

        if self.stub_mode:
            payload = self._stub_payload(b, q)
            self._emit(0.0, True)
            return payload

        if not self.breaker.allow_request():
            payload = self._stub_payload(b, q)
            payload["circuit_open"] = True
            self._emit(0.0, False)
            return payload

        self.limiter.acquire()  # stub: counted, never blocks local run
        started = time.perf_counter()
        try:
            raw = self._fetch_raw(b, q)
        except ProviderError:
            try:
                raw = self._fetch_yahoo(b, q)
            except ProviderError:
                self.breaker.record_failure()
                self._emit((time.perf_counter() - started) * 1000, False)
                payload = self._stub_payload(b, q)
                payload["circuit_open"] = self.breaker.state != CircuitBreaker.CLOSED
                return payload
        self.breaker.record_success()
        self._emit((time.perf_counter() - started) * 1000, True)
        rate = float(raw["rate"])
        payload = {
            "pair": f"{b}/{q}",
            "base": b,
            "quote": q,
            "rate": rate,
            "inverse": 1.0 / rate,
            "as_of": raw.get("as_of") or _utcnow(),
            "source": raw.get("source", LIVE_SOURCE),
            "delay_minutes": self.delay_minutes,
            "missing_fields": [],
            "fallback_used": False,
        }
        self._cache_put(cache_key, payload)
        return payload

    def provenance_for(self, payload: dict) -> Provenance:
        """Build the standard provenance envelope for a ``get_rate`` payload."""
        as_of = payload.get("as_of") or _utcnow()
        if isinstance(as_of, str):
            try:
                as_of = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
            except ValueError:
                as_of = _utcnow()
        if not isinstance(as_of, datetime):
            as_of = _utcnow()
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=timezone.utc)
        age_min = max(0.0, (_utcnow() - as_of).total_seconds() / 60)
        fallback = bool(payload.get("fallback_used", False))
        grade, _reasons = grade_quality(
            delay_minutes=self.delay_minutes,
            age_minutes=age_min,
            missing_fields=payload.get("missing_fields", []),
            fallback_used=fallback,
            reconciled=False,  # single FX source in v1
        )
        return build_provenance(
            payload.get("source", self.name),
            as_of=as_of,
            delay_minutes=self.delay_minutes,
            quality_grade=grade,
            fallback_used=fallback,
            missing_fields=payload.get("missing_fields", []),
        )
