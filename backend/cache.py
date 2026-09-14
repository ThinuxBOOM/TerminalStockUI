"""Cache-ready interface: Redis when configured, in-memory otherwise.

No hard dependency for local run: `redis` is imported lazily and any
failure falls back to the in-memory backend (logged, never raised).

Vercel/serverless: memory fallback is the default (no REDIS_URL needed).
Set UPSTASH_REDIS_URL (preferred on Vercel) or REDIS_URL to opt into Redis;
both are read lazily at first get_cache() call, never at module import.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Protocol

log = logging.getLogger(__name__)


class CacheBackend(Protocol):
    def get(self, key: str) -> object | None: ...
    def set(self, key: str, value: object, ttl_s: int = 300) -> None: ...
    def delete(self, key: str) -> None: ...


class InMemoryCache:
    """TTL dict cache. Namespaced keys: '<ns>:<key>' by convention.

    Bounded: at most ``MAX_ENTRIES`` keys (expired entries are purged
    first, then oldest-inserted). Prevents unbounded growth on
    long-lived processes while keeping get/set/delete semantics.
    """

    MAX_ENTRIES = 1000

    def __init__(self, max_entries: int = MAX_ENTRIES) -> None:
        self._store: dict[str, tuple[float, object]] = {}
        self._max_entries = max(1, int(max_entries))

    def get(self, key: str) -> object | None:
        try:
            hit = self._store.get(key)
        except TypeError:
            return None
        if not hit:
            return None
        expires_at, value = hit
        if expires_at < time.monotonic():
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: object, ttl_s: int = 300) -> None:
        try:
            ttl = int(ttl_s)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            ttl = 300
        # Guard NaN/inf/negative TTLs (max(1, nan) is unreliable).
        try:
            import math as _math

            if not _math.isfinite(float(ttl)):
                ttl = 300
        except (TypeError, ValueError, OverflowError):
            ttl = 300
        self._store[key] = (time.monotonic() + max(1, ttl), value)
        self._evict_if_needed()

    def delete(self, key: str) -> None:
        try:
            self._store.pop(key, None)
        except TypeError:
            return

    def clear(self) -> None:
        self._store.clear()

    def _evict_if_needed(self) -> None:
        if len(self._store) <= self._max_entries:
            return
        now = time.monotonic()
        expired = [k for k, (exp, _) in self._store.items() if exp < now]
        for k in expired:
            self._store.pop(k, None)
            if len(self._store) <= self._max_entries:
                return
        while len(self._store) > self._max_entries:
            oldest = next(iter(self._store))
            self._store.pop(oldest, None)


class RedisCache:
    """Thin redis wrapper with graceful fallback to memory."""

    def __init__(self, url: str) -> None:
        import json as _json

        import redis as _redis

        self._json = _json
        self._client = _redis.Redis.from_url(url, decode_responses=True)
        self._fallback = InMemoryCache()

    def get(self, key: str) -> object | None:
        try:
            raw = self._client.get(key)
            return self._json.loads(raw) if raw is not None else None
        except Exception as exc:
            log.warning("redis GET failed, using memory fallback: %s", exc)
            return self._fallback.get(key)

    def set(self, key: str, value: object, ttl_s: int = 300) -> None:
        try:
            self._client.set(key, self._json.dumps(value, default=str), ex=max(1, ttl_s))
        except Exception as exc:
            log.warning("redis SET failed, using memory fallback: %s", exc)
            self._fallback.set(key, value, ttl_s)

    def delete(self, key: str) -> None:
        try:
            self._client.delete(key)
        except Exception:
            self._fallback.delete(key)

    def clear(self) -> None:
        """Best-effort cache flush (tests + admin). Never raises."""
        try:
            self._fallback.clear()
        except Exception:
            pass
        try:
            self._client.flushdb()
        except Exception:
            pass


_cache: CacheBackend | None = None


def get_cache() -> CacheBackend:
    """Singleton: Redis if REDIS_URL/UPSTASH_REDIS_URL is set and importable, else memory."""
    global _cache
    if _cache is not None:
        return _cache
    url = os.getenv("REDIS_URL", "") or os.getenv("UPSTASH_REDIS_URL", "")
    if url:
        try:
            _cache = RedisCache(url)
            return _cache
        except Exception as exc:  # pragma: no cover
            log.warning("REDIS_URL set but redis unavailable (%s); using memory cache", exc)
    _cache = InMemoryCache()
    return _cache
