"""Provider base: errors, circuit breaker, rate-limit stub, interface."""

from __future__ import annotations

import time
from collections import deque


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
