"""Provider base: errors, circuit breaker, rate-limit stub, interface."""

from __future__ import annotations

import time
from collections import deque
from typing import Protocol


class ProviderError(RuntimeError):
    """Upstream provider failure (timeout, HTTP error, parse error)."""

    def __init__(self, provider: str, message: str, *, retryable: bool = True) -> None:
        super().__init__(f"{provider}: {message}")
        self.provider = provider
        self.retryable = retryable


class CircuitOpenError(ProviderError):
    """Raised when the circuit is open and no fallback is permitted."""

    def __init__(self, provider: str) -> None:
        super().__init__(provider, "circuit open", retryable=True)


class CircuitBreaker:
    """closed -> open after N consecutive failures; half-open probe after timeout."""

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


class QuoteProvider(Protocol):
    name: str

    def get_quote(self, symbol: str) -> dict:
        """Return a *normalized* quote dict including missing_fields. ..."""
