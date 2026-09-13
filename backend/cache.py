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
    """TTL dict cache. Namespaced keys: '<ns>:<key>' by convention."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[float, object]] = {}

    def get(self, key: str) -> object | None:
        hit = self._store.get(key)
        if not hit:
            return None
        expires_at, value = hit
        if expires_at < time.monotonic():
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: object, ttl_s: int = 300) -> None:
        self._store[key] = (time.monotonic() + max(1, ttl_s), value)

    def delete(self, key: str) -> None:
        self._store.pop(key, None)


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
