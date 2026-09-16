"""SEC EDGAR statement provider (free authoritative US fundamentals).

No API key. Two public endpoints (both require a descriptive User-Agent
contact string or the SEC returns 403; fair use is 10 req/s):

- ``https://www.sec.gov/files/company_tickers.json`` — ticker -> CIK map
  (cached 7 days, in-memory bounded; seed map covers the registry universe
  when the map fetch fails).
- ``https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json`` —
  every XBRL fact for one filer (cached 24h, in-memory bounded).

Annual discipline lives in :mod:`concepts` (10-K/20-F/40-F only,
350-380-day flows, latest-filed-wins restatements, revenue-anchored ends).

Fail-closed: any outage/unknown ticker/missing dep raises
:class:`ProviderError` — statement numbers are NEVER stubbed (a fabricated
balance sheet would poison every quality score downstream). Same
resilience shape as the quote providers otherwise: circuit breaker,
token-bucket limiter (8 rps stays under the SEC 10 rps fair-use cap),
tenacity retry on transient failures, health hook.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

from backend.market_data.providers.base import CircuitBreaker, ProviderError, RateLimiter
from backend.market_data.statements import concepts

NAME = "sec-edgar"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{}.json"
CIK_TTL_S = 7 * 24 * 3600
FACTS_TTL_S = 24 * 3600
CACHE_MAX_ENTRIES = 256

#: Seed ticker -> CIK for the registry universe (used when the SEC map fetch
#: fails; CIKs are stable identifiers). Non-US venues have no CIK (None).
SEED_CIK: dict[str, int | None] = {
    "AAPL": 320193,
    "MSFT": 789019,
    "NVDA": 1045810,
    "JPM": 19617,
    "AAP": 931244,
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _user_agent() -> str:
    try:
        contact = (os.getenv("STATEMENTS_CONTACT") or "").strip()
    except Exception:
        contact = ""
    if not contact:
        contact = "onemarket-analyzer"
    return f"OneMarketAnalyzer/1.0 ({contact})"


def _norm_ticker(text: str) -> str:
    """Normalize for ticker comparison (BRK.B / BRK-B / BRK B all match)."""
    out = (text or "").strip().upper()
    for sep in (".", "-", " ", "/", "_"):
        out = out.replace(sep, "")
    return out


class SecEdgarProvider:
    """Live SEC EDGAR XBRL provider (no key). Fail-closed, never stubbed."""

    name = NAME

    def __init__(
        self,
        *,
        breaker: CircuitBreaker | None = None,
        limiter: RateLimiter | None = None,
        stub_mode: bool = False,
        on_call: object | None = None,
        timeout_s: float = 15.0,
    ) -> None:
        self.breaker = breaker or CircuitBreaker()
        # SEC fair use: 10 req/s — stay under it with headroom.
        self.limiter = limiter or RateLimiter(rate_per_sec=8.0, burst=8)
        self.stub_mode = stub_mode
        self.timeout_s = timeout_s
        self._on_call = on_call
        self._cik_entry: tuple[float, dict[str, int]] | None = None
        self._facts_cache: dict[str, tuple[float, dict]] = {}

    # -- internals ------------------------------------------------------
    def _emit(self, latency_ms: float, ok: bool, *, error=None, status_code=None) -> None:
        from backend.market_data.providers.base import emit_health as _emit_health

        _emit_health(self._on_call, self.name, latency_ms, ok,
                     error=error, status_code=status_code)

    def _cache_get(self, store: dict, key: str) -> dict | None:
        try:
            entry = store.get(key)
        except Exception:
            return None
        if not entry:
            return None
        expires_at, payload = entry
        if time.monotonic() >= expires_at:
            try:
                store.pop(key, None)
            except Exception:
                pass
            return None
        return payload

    def _cache_put(self, store: dict, key: str, payload: dict, ttl_s: int) -> None:
        try:
            store[key] = (time.monotonic() + ttl_s, payload)
            while len(store) > CACHE_MAX_ENTRIES:
                store.pop(next(iter(store)), None)
        except Exception:
            pass

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_fixed(1),
        retry=retry_if_exception_type(ProviderError),
        reraise=True,
    )
    def _http_get_json(self, url: str) -> dict:
        try:  # lazy: offline/test envs stay import-safe
            import httpx  # type: ignore[import-not-found]
        except Exception as exc:
            raise ProviderError(NAME, "httpx package unavailable") from exc
        try:
            resp = httpx.get(
                url,
                headers={"User-Agent": _user_agent(), "Accept-Encoding": "gzip"},
                timeout=self.timeout_s,
                follow_redirects=True,
            )
        except Exception as exc:  # network / timeout
            raise ProviderError(NAME, f"{type(exc).__name__}: {exc}") from exc
        try:
            status = int(getattr(resp, "status_code", 200) or 200)
        except (TypeError, ValueError):
            status = 200
        if status == 403:
            raise ProviderError(NAME, "HTTP 403 (missing/invalid User-Agent?)")
        if status == 404:
            raise ProviderError(NAME, "HTTP 404 (unknown CIK?)", retryable=False)
        if status == 429:
            raise ProviderError(NAME, "rate limited (429)")
        if status >= 400:
            raise ProviderError(NAME, f"HTTP {status}")
        try:
            data = resp.json()
        except Exception as exc:
            raise ProviderError(NAME, f"{type(exc).__name__}: bad JSON") from exc
        if not isinstance(data, dict):
            raise ProviderError(NAME, "unexpected SEC schema (not an object)")
        return data

    def _ticker_map(self) -> dict[str, int]:
        """Ticker -> CIK from the SEC map (cached); seed fallback on failure."""
        hit = self._cik_entry
        if hit is not None:
            expires_at, table = hit
            if time.monotonic() < expires_at:
                return dict(table)
        try:
            raw = self._http_get_json(TICKERS_URL)
            table: dict[str, int] = {}
            for entry in (raw or {}).values():
                if not isinstance(entry, dict):
                    continue
                try:
                    ticker = str(entry.get("ticker") or "").strip().upper()
                    cik = int(entry.get("cik_str"))  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    continue
                if ticker and cik > 0:
                    table[_norm_ticker(ticker)] = cik
            if not table:
                raise ProviderError(NAME, "empty SEC ticker map")
            self._cik_entry = (time.monotonic() + CIK_TTL_S, table)
            return dict(table)
        except ProviderError:
            seed = {k: v for k, v in SEED_CIK.items() if isinstance(v, int)}
            if not seed:
                raise
            return dict(seed)

    def resolve_cik(self, symbol: str) -> int:
        """Resolve a US ticker to its CIK (raises ProviderError when unknown)."""
        text = (symbol or "").strip().upper()
        if not text:
            raise ProviderError(NAME, "empty symbol", retryable=False)
        # Strip Yahoo suffixes defensively (US symbols are bare; "BRK.B"
        # keeps its dot — only venue suffixes like .SS/.PA are stripped).
        base = text
        for suffix in (".SS", ".PA", ".AS", ".BR"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
        table = self._ticker_map()
        cik = table.get(_norm_ticker(base))
        if cik is None:
            # Seed fallback for the registry universe (map may be stale).
            seed = SEED_CIK.get(base)
            if isinstance(seed, int):
                return seed
            raise ProviderError(NAME, f"no CIK for {text!r}", retryable=False)
        return int(cik)

    def _companyfacts(self, cik: int) -> dict:
        key = f"CIK{cik:010d}"
        hit = self._cache_get(self._facts_cache, key)
        if hit is not None:
            return hit
        if not self.breaker.allow_request():
            raise ProviderError(NAME, "circuit open (breaker)")
        self.limiter.acquire()  # counted, never blocks
        started = time.perf_counter()
        try:
            data = self._http_get_json(COMPANYFACTS_URL.format(f"{cik:010d}"))
        except ProviderError as exc:
            self.breaker.record_failure()
            self._emit((time.perf_counter() - started) * 1000, False,
                       error=f"{type(exc).__name__}: {exc}")
            raise
        self.breaker.record_success()
        self._emit((time.perf_counter() - started) * 1000, True)
        self._cache_put(self._facts_cache, key, data, FACTS_TTL_S)
        return data

    # -- public ---------------------------------------------------------
    def get_annual_statements(self, symbol: str) -> tuple[dict, dict]:
        """Annual current+prior statement mapping + info envelope.

        Returns ``(mapping, info)`` where mapping holds canonical raw fields
        (``revenue``/``revenue_prior``/… — derived ratios live in the
        resolver so both vendors share them) and info holds
        ``{source, cik, currency, fiscal_ends, filed_as_of, forms}``.
        Raises :class:`ProviderError` on any failure (never stub numbers).
        """
        if self.stub_mode:
            self._emit(0.0, False, error="stub mode: statements never stubbed")
            raise ProviderError(NAME, "stub mode: no live statements")
        upper = (symbol or "").strip().upper()
        if not upper:
            raise ProviderError(NAME, "empty symbol", retryable=False)
        cik = self.resolve_cik(upper)
        facts = self._companyfacts(cik)
        ends, currency = concepts.anchor_ends(facts)
        if not ends:
            raise ProviderError(NAME, f"no annual revenue facts for {upper!r}")
        currency = currency or "USD"
        mapping: dict[str, float] = {}
        filed_as_of: str | None = None
        forms: list[str] = []
        for metric in concepts.CONCEPT_PRIORITY:
            try:
                series, _unit = concepts.pick_metric_series(
                    facts, metric, prefer_currency=currency, prefer_ends=ends
                )
            except Exception:
                continue
            if not series:
                continue
            try:
                mapping[metric] = float(series[0][1])
                if len(series) > 1:
                    mapping[f"{metric}_prior"] = float(series[1][1])
                # Provenance from the anchor (revenue) current-year fact.
                if metric == "revenue":
                    anchor_fact = series[0][2] or {}
                    try:
                        filed_as_of = str(anchor_fact.get("filed") or "")[:10] or None
                    except Exception:
                        filed_as_of = None
                    try:
                        form = str(anchor_fact.get("form") or "").strip()
                        if form:
                            forms = [form]
                    except Exception:
                        pass
            except (TypeError, ValueError, IndexError, KeyError):
                continue
        if "revenue" not in mapping:
            raise ProviderError(NAME, f"no annual revenue facts for {upper!r}")
        info = {
            "source": NAME,
            "cik": cik,
            "currency": currency,
            "fiscal_ends": ends[:2],
            "filed_as_of": filed_as_of,
            "forms": forms,
        }
        return mapping, info


__all__ = ["SecEdgarProvider", "NAME", "SEED_CIK"]
