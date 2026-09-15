"""Provider base: errors, circuit breaker, rate-limit stub, interface."""

from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime, timezone


class ProviderError(RuntimeError):
    """Upstream provider failure (timeout, HTTP error, parse error)."""

    def __init__(self, provider: str, message: str, *, retryable: bool = True) -> None:
        super().__init__(f"{provider}: {message}")
        self.provider = provider
        self.retryable = retryable


def extract_status_code(error: object) -> int | None:
    """Best-effort HTTP status from a ProviderError message (429/401/...).

    Passive health hooks use this so 429 (quota) can be marked degraded
    rather than down without threading response objects through _emit.
    Never raises.
    """
    try:
        text = str(error or "").lower()
    except Exception:
        return None
    if "429" in text or "rate limit" in text or "too many requests" in text:
        return 429
    if "401" in text or "unauthorized" in text:
        return 401
    if "403" in text or "forbidden" in text:
        return 403
    if "404" in text:
        return 404
    return None


def emit_health(
    on_call: object | None,
    provider: str,
    latency_ms: float,
    ok: bool,
    *,
    error: object | None = None,
    status_code: object | None = None,
) -> None:
    """Quota-aware health emit (passive metrics). Never raises.

    Forwards ``(provider, latency_ms, ok, status_code=, error=)`` when the
    hook supports it (new tracker), else falls back to the legacy 3-arg
    ``(provider, latency_ms, ok)`` call. Providers should pass the caught
    ProviderError text so the tracker can classify 429 -> degraded.
    """
    if on_call is None:
        return
    try:
        err_text = None
        if error is not None:
            try:
                err_text = str(error)[:280] or None
            except Exception:
                err_text = None
        code = status_code
        if code is None and err_text:
            try:
                code = extract_status_code(err_text)
            except Exception:
                code = None
        try:
            on_call(provider, float(latency_ms), bool(ok),  # type: ignore[misc]
                    status_code=code, error=err_text)
        except TypeError:
            on_call(provider, float(latency_ms), bool(ok))  # type: ignore[misc]
    except Exception:
        pass


class CircuitBreaker:
    """closed -> open after N consecutive failures; half-open probe after timeout.

    Tracker-level breaker (backend.market_data.health.ProviderHealthTracker)
    mirrors this with hyphenated "half-open" + 5 fails/5m -> open 60s.
    """

    CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"

    def __init__(self, failure_threshold: int = 5, reset_timeout_s: float = 60.0) -> None:
        self.failure_threshold = failure_threshold
        self.reset_timeout_s = reset_timeout_s
        self.state = self.CLOSED
        self.consecutive_failures = 0
        self.opened_at: float | None = None

    def allow_request(self) -> bool:
        if self.state == self.CLOSED:
            return True
        if self.state == self.OPEN:
            if self.opened_at and (time.monotonic() - self.opened_at) >= self.reset_timeout_s:
                self.state = self.HALF_OPEN
                return True
            return False
        return True  # half-open: permit the probe

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.state = self.CLOSED
        self.opened_at = None

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        if self.state == self.HALF_OPEN or self.consecutive_failures >= self.failure_threshold:
            self.state = self.OPEN
            self.opened_at = time.monotonic()


class RateLimiter:
    """Token-bucket stub (local). Production uses the Redis-backed bucket."""

    def __init__(self, rate_per_sec: float = 5.0, burst: int = 10) -> None:
        self.rate_per_sec = rate_per_sec
        self.burst = burst
        self._tokens = float(burst)
        self._updated = time.monotonic()
        self.rejected = 0

    def acquire(self) -> bool:
        now = time.monotonic()
        self._tokens = min(self.burst, self._tokens + (now - self._updated) * self.rate_per_sec)
        self._updated = now
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        self.rejected += 1
        return False


class QuotaLimiter:
    """Client-side per-minute + per-day call caps (thread-safe, fail-fast).

    TwelveData free Basic is ruthless about quotas, so quotes budget
    6/min + 600/day (leaving headroom under the vendor 8/min + 800/day for
    bars/history calls on the same key); Alpaca is generous (200/min free)
    and capped at 150/min. acquire() records an attempt and returns True,
    or False when a cap is hit (``rejected``/``rejected_day`` counters for
    observability). Never raises, never blocks: providers fail fast with a
    429-style ProviderError so the service chain falls through to the next
    live source instead of waiting out the window.

    Sliding minute window on the monotonic clock + fixed UTC-day buckets;
    bounded memory (minute deque capped at the per-minute allowance, day map
    pruned to recent days). A limiter bug must never break the call path, so
    any internal error fails OPEN (admits).

    Per-process scope: on serverless each instance enforces its own share.
    That under-admits per instance (safe direction for a single warm
    instance) but N warm instances can sum past the vendor cap — exact
    global enforcement needs atomic Redis counters (the cache API has no
    incr primitive today; future work).
    """

    def __init__(
        self,
        calls_per_minute: int | float | None = None,
        calls_per_day: int | float | None = None,
        name: str = "quota",
    ) -> None:
        self.name = str(name or "quota")
        self._per_minute = self._clean_cap(calls_per_minute)
        self._per_day = self._clean_cap(calls_per_day)
        self._lock = threading.Lock()
        self._minute_hits: deque[float] = deque()
        self._day_counts: dict[str, int] = {}
        self.rejected = 0
        self.rejected_day = 0

    @staticmethod
    def _clean_cap(value: int | float | None) -> int | None:
        try:
            if value is None or isinstance(value, bool):
                return None
            number = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError, OverflowError):
            return None
        return number if number > 0 else None

    def acquire(self) -> bool:
        try:
            now_m = time.monotonic()
            with self._lock:
                if self._per_minute is not None:
                    cutoff = now_m - 60.0
                    hits = self._minute_hits
                    while hits and hits[0] <= cutoff:
                        hits.popleft()
                    if len(hits) >= self._per_minute:
                        self.rejected += 1
                        return False
                day_key: str | None = None
                if self._per_day is not None:
                    try:
                        day_key = datetime.now(timezone.utc).date().isoformat()
                    except Exception:
                        day_key = None
                    if day_key is not None and self._day_counts.get(day_key, 0) >= self._per_day:
                        self.rejected += 1
                        self.rejected_day += 1
                        return False
                if self._per_minute is not None:
                    self._minute_hits.append(now_m)
                    while len(self._minute_hits) > self._per_minute:
                        self._minute_hits.popleft()
                if self._per_day is not None and day_key is not None:
                    self._day_counts[day_key] = self._day_counts.get(day_key, 0) + 1
                    if len(self._day_counts) > 3:
                        for old in sorted(self._day_counts)[:-2]:
                            self._day_counts.pop(old, None)
                return True
        except Exception:
            return True

    def status(self) -> dict:
        """Current quota snapshot (never raises; all values best-effort)."""
        try:
            now_m = time.monotonic()
            with self._lock:
                minute_used = 0
                if self._per_minute is not None:
                    cutoff = now_m - 60.0
                    minute_used = sum(1 for t in self._minute_hits if t > cutoff)
                try:
                    today = datetime.now(timezone.utc).date().isoformat()
                except Exception:
                    today = ""
                return {
                    "name": self.name,
                    "calls_per_minute": self._per_minute,
                    "calls_per_day": self._per_day,
                    "minute_used": minute_used,
                    "day_used": int(self._day_counts.get(today, 0)) if today else 0,
                    "day": today,
                    "rejected": int(self.rejected),
                    "rejected_day": int(self.rejected_day),
                }
        except Exception:
            return {"name": getattr(self, "name", "quota")}
