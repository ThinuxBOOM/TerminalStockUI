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
- reconciler (Phase 1c): live frankfurter/yahoo rates are cross-checked
  against the ECB eurofxref daily reference (``ECB_URL``, parsed per-EUR
  ``Cube`` rates, 1h in-memory table cache). Agreement within
  ``RECONCILE_TOLERANCE`` (0.5%) stamps ``reconciled=True`` (grade A);
  disagreement keeps ``reconciled=False`` (grade B) with ``ecb_rate`` +
  ``divergence_pct`` transparency fields; any ECB failure abstains silently
  (``reconciled=False``, no ecb fields) and never blocks the live rate;
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

import math
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
#: ECB eurofxref daily reference file (independent reconciler, Phase 1c).
ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
#: How long a parsed ECB table is reused before refetching. The cached entry
#: carries the ECB TIME date with it, so an ECB date rollover is picked up on
#: the first live fetch after expiry (worst-case reconciler staleness: 1h).
ECB_TTL_S = 3600
#: Reconciliation tolerance: live and ECB cross rates agree when
#: ``abs(live / ecb - 1) <= RECONCILE_TOLERANCE`` (0.5%).
RECONCILE_TOLERANCE = 0.005
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


def _parse_ecb_xml(text: str) -> tuple[dict[str, float], str | None]:
    """Parse an ECB eurofxref XML document.

    Returns ``(per_eur_table, time_str)`` where the table maps currency codes
    to units-per-EUR (always including ``EUR: 1.0``) and ``time_str`` is the
    ECB ``TIME`` date (``YYYY-MM-DD``) or ``None`` when absent. Namespace
    agnostic: matches any ``Cube`` element by local name. Raises
    :class:`ValueError` when no usable rates are found.
    """
    import xml.etree.ElementTree as ET

    root = ET.fromstring(text)
    table: dict[str, float] = {"EUR": 1.0}
    day: str | None = None
    for elem in root.iter():
        tag = elem.tag
        if not (isinstance(tag, str) and tag.rsplit("}", 1)[-1] == "Cube"):
            continue
        stamp = elem.attrib.get("time")
        if stamp is not None and day is None:
            day = stamp
        ccy = elem.attrib.get("currency")
        raw_rate = elem.attrib.get("rate")
        if ccy and raw_rate:
            try:
                value = float(raw_rate)
            except (TypeError, ValueError):
                continue
            if value > 0:
                table[str(ccy).strip().upper()] = value
    if len(table) < 2:  # EUR alone means the feed carried no rates
        raise ValueError("ECB feed contained no currency rates")
    return table, day


def _fetch_ecb_table() -> tuple[dict[str, float], str | None] | None:
    """Best-effort fetch of the ECB daily reference table (Phase 1c reconciler).

    Lazy ``httpx`` import, 5s timeout, no retry. Returns ``None`` on ANY
    failure (missing dep, network/HTTP error, unparseable body) — the caller
    treats ``None`` as abstention and serves the live rate unreconciled.
    Never raises.
    """
    try:  # lazy: never imported at module load; offline envs stay import-safe
        import httpx  # type: ignore[import-not-found]
    except Exception:
        return None
    try:
        resp = httpx.get(ECB_URL, timeout=5.0)
        resp.raise_for_status()
        return _parse_ecb_xml(resp.text)
    except Exception:
        return None


class FXProvider:
    """Live Frankfurter/ECB provider with yfinance secondary + stub fallback.

    Chain per pair: frankfurter -> yfinance FX ticker -> flagged stub.
    Never crashes offline."""

    def _fetch_yahoo(self, base: str, quote: str) -> dict:
        """Secondary live fetch via yfinance FX tickers (``EURUSD=X`` …).

        Tries the direct pair first, then the inverse pair (inverted).
        Raises :class:`ProviderError` when yfinance is unavailable or both
        tickers yield no usable close — the caller then serves the stub.
        as_of is the MARKET bar timestamp (last hist index), not fetch time,
        so weekend/holiday closes are not misgraded as fresh.
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
                if not (math.isfinite(close) and close > 0):
                    raise ProviderError(NAME, f"bad close for {symbol}")
            except ProviderError as exc:
                last_exc = exc
                continue
            except Exception as exc:  # network / parse failure
                last_exc = exc
                continue
            rate = close if not invert else 1.0 / close
            # Market-time stamp: last bar index, not fetch wall-clock.
            try:
                bar_ts = hist.index[-1]
                if hasattr(bar_ts, "to_pydatetime"):
                    bar_dt = bar_ts.to_pydatetime()
                else:
                    from datetime import datetime as _dt
                    bar_dt = _dt.fromisoformat(str(bar_ts))
                from datetime import timezone as _tz
                if bar_dt.tzinfo is None:
                    bar_dt = bar_dt.replace(tzinfo=_tz.utc)
                as_of = bar_dt
            except Exception:
                as_of = _utcnow()
            return {
                "base": base,
                "quote": quote,
                "rate": rate,
                "as_of": as_of,
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
        try:
            ttl = int(cache_ttl_s)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            ttl = CACHE_TTL_S
        try:
            import math as _math

            if not _math.isfinite(float(ttl)):
                ttl = CACHE_TTL_S
        except (TypeError, ValueError, OverflowError):
            ttl = CACHE_TTL_S
        self.cache_ttl_s = ttl
        self.stub_mode = stub_mode
        self._on_call = on_call  # health hook: fn(provider, latency_ms, ok)
        self._cache: dict[str, tuple[float, dict]] = {}
        # Rate cache is bounded (live pairs are USD/EUR/CNY only); expired
        # entries are purged first, then oldest-inserted, so the dict cannot
        # grow without bound on long-lived processes.
        self._cache_max_entries = 64
        # ECB reconciler table: (expires_at_monotonic, ecb_time_date, per_eur).
        # Only successful fetches are cached (failures abstain and retry on the
        # next live fetch); the entry is date-stamped so a date rollover is
        # picked up once the 1h TTL expires.
        self._ecb_entry: tuple[float, str | None, dict[str, float]] | None = None

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
        if len(self._cache) <= self._cache_max_entries:
            return
        now = time.monotonic()
        for stale in [k for k, (exp, _) in self._cache.items() if now >= exp]:
            self._cache.pop(stale, None)
            if len(self._cache) <= self._cache_max_entries:
                return
        while len(self._cache) > self._cache_max_entries:
            self._cache.pop(next(iter(self._cache)), None)

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

    def _get_ecb_table(self) -> dict[str, float] | None:
        """Return cached ECB per-EUR table, refetching when the 1h TTL lapses.

        ``None`` means the reconciler abstains (unavailable/unparseable/pair
        uncovered is decided by the caller). Never raises.
        """
        try:
            now = time.monotonic()
            entry = self._ecb_entry
            if entry is not None:
                expires_at, _day, table = entry
                if now < expires_at:
                    return dict(table)
            fetched = _fetch_ecb_table()
            if fetched is None:
                return None
            table, day = fetched
            self._ecb_entry = (now + ECB_TTL_S, day, dict(table))
            return dict(table)
        except Exception:
            return None

    def _reconcile_live_rate(self, base: str, quote: str, live_rate: float) -> dict:
        """Cross-check a live rate against the ECB daily reference (Phase 1c).

        Agree (``abs(live/ecb - 1) <= RECONCILE_TOLERANCE``) ->
        ``{"reconciled": True, "ecb_rate": ...}`` (fresh rates grade A).
        Disagree -> ``{"reconciled": False, "ecb_rate": ...,
        "divergence_pct": ...}`` with ``divergence_pct`` in percent points
        (``abs(live/ecb - 1) * 100``); grade stays B, divergence is
        transparency-only here (persistent audit-logging is out of scope).
        Abstain (ECB down/unparseable/pair uncovered) ->
        ``{"reconciled": False}`` with NO ``ecb_rate``/``divergence_pct`` keys,
        so abstention is distinguishable from disagreement and the payload
        grades B exactly as before reconciliation existed. Never raises.
        """
        try:
            if not (live_rate > 0):
                return {"reconciled": False}
            if not math.isfinite(live_rate):
                return {"reconciled": False}
            table = self._get_ecb_table()
            if not table:
                return {"reconciled": False}
            base_rate = table.get(base)
            quote_rate = table.get(quote)
            if base_rate is None or quote_rate is None or base_rate <= 0:
                return {"reconciled": False}
            ecb_rate = quote_rate / base_rate
            if not (ecb_rate > 0):
                return {"reconciled": False}
            if abs(live_rate / ecb_rate - 1.0) <= RECONCILE_TOLERANCE:
                return {"reconciled": True, "ecb_rate": ecb_rate}
            return {
                "reconciled": False,
                "ecb_rate": ecb_rate,
                "divergence_pct": abs(live_rate / ecb_rate - 1.0) * 100.0,
            }
        except Exception:
            return {"reconciled": False}

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
            if not math.isfinite(rate) or rate <= 0:
                raise ValueError("non-positive rate")
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError(NAME, f"unexpected frankfurter schema: {exc}") from exc
        # Market-time stamp: Frankfurter/ECB reference date when present,
        # not fetch wall-clock (a Tuesday fetch of Monday's fix must not
        # grade as fresh Tuesday).
        as_of = _utcnow()
        try:
            stamp = data.get("date")
            if stamp:
                from datetime import datetime as _dt2
                from datetime import timezone as _tz2
                parsed = _dt2.fromisoformat(str(stamp)[:10])
                as_of = parsed.replace(tzinfo=_tz2.utc)
        except Exception:
            as_of = _utcnow()
        return {
            "base": base,
            "quote": quote,
            "rate": rate,
            "as_of": as_of,
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
                # Trivially reconciled: no ECB fetch is attempted.
                "reconciled": True,
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
        try:
            rate = float(raw["rate"])
        except (KeyError, TypeError, ValueError):
            rate = float("nan")
        if not math.isfinite(rate) or rate <= 0:
            # Malformed/non-finite upstream rate: degrade to the flagged
            # stub instead of leaking NaN/inf (get_rate never raises here).
            self.breaker.record_failure()
            self._emit((time.perf_counter() - started) * 1000, False)
            payload = self._stub_payload(b, q)
            payload["circuit_open"] = self.breaker.state != CircuitBreaker.CLOSED
            return payload
        self.breaker.record_success()
        self._emit((time.perf_counter() - started) * 1000, True)
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
        # Phase 1c: cross-check frankfurter/yahoo success against the cached
        # ECB daily reference. Abstains silently (reconciled False, no ecb
        # fields) when ECB is unreachable — never blocks the live rate.
        payload.update(self._reconcile_live_rate(b, q, rate))
        self._cache_put(cache_key, payload)
        return payload

    def provenance_for(self, payload: dict, reconciled: bool = False) -> Provenance:
        """Build the standard provenance envelope for a ``get_rate`` payload.

        ``reconciled`` defaults to ``False`` so existing callers keep grading
        fresh single-source rates B; :meth:`get_rate` stamps live payloads
        with its own ``"reconciled"`` flag, which is honored when the caller
        does not pass the keyword (so the unchanged ``/api/fx`` router flows
        agree->A through). An explicit keyword ORs with the payload flag.
        """
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
        flag = bool(reconciled or payload.get("reconciled", False))
        grade, _reasons = grade_quality(
            delay_minutes=self.delay_minutes,
            age_minutes=age_min,
            missing_fields=payload.get("missing_fields", []),
            fallback_used=fallback,
            reconciled=flag,
        )
        return build_provenance(
            payload.get("source", self.name),
            as_of=as_of,
            delay_minutes=self.delay_minutes,
            quality_grade=grade,
            fallback_used=fallback,
            missing_fields=payload.get("missing_fields", []),
        )
