"""DB engine/session. DATABASE_URL -> Postgres; SQLite fallback for tests/local.

Serverless note (Vercel + Supabase): pooled connections do not survive across
function invocations, so when ``DATABASE_URL`` points at the Supabase
connection pooler (contains ``pgbouncer`` or port ``:6543``) or
``APP_ENV=production`` (or ``VERCEL=1``), the engine uses ``NullPool``
(open-per-request, no reuse). Otherwise a short-lived ``QueuePool``
(``pool_size=5, max_overflow=5``) is used. ``pool_pre_ping=True`` is always
set for Postgres so stale pooled connections are recycled transparently.
SQLite fallback is unchanged (file ``./onemarket.db`` or ``:memory:``).
"""

from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from .models import Base

_engine = None
_SessionLocal = None
#: Engines for explicit URLs (ingest/tests), keyed by URL so repeated calls
#: with the same URL reuse one engine instead of creating a new pool per
#: call. Cleared by reset_engine() alongside the default cached engine.
_engines_by_url: dict[str, object] = {}
#: Session factories for explicit URLs (same caching rationale as engines:
#: avoid per-call sessionmaker construction; cleared by reset_engine()).
_session_factories_by_url: dict[str, object] = {}


def database_url() -> str:
    raw = os.getenv("DATABASE_URL", "")
    text = (raw or "").strip()
    if not text:
        # Fail-closed in production/serverless: an ephemeral SQLite file on
        # Vercel (read-only fs, per-invocation) would serve an empty DB and
        # fan out into a live-fetch storm. Require DATABASE_URL when
        # APP_ENV=production or VERCEL=1; local dev/tests keep the file fallback.
        try:
            app_env = os.getenv("APP_ENV", "").strip().lower()
            vercel = os.getenv("VERCEL", "").strip() == "1"
        except Exception:
            app_env, vercel = "", False
        if app_env == "production" or vercel:
            raise RuntimeError("DATABASE_URL is required in production (no SQLite fallback on serverless)")
        return "sqlite:///./onemarket.db"
    return text


def _is_serverless_postgres(url: str) -> bool:
    """True when the URL should use NullPool (Supabase pooler / Vercel prod)."""
    lowered = (url or "").lower()
    if "pgbouncer" in lowered or ":6543" in lowered:
        return True
    if os.getenv("APP_ENV", "").strip().lower() == "production":
        return True
    if os.getenv("VERCEL", "").strip() == "1":
        return True
    return False


def _normalize_postgres_url(url: str) -> str:
    """Accept bare ``postgresql://`` / ``postgres://`` (e.g. Supabase dashboard).

    SQLAlchemy 2.0 + ``psycopg[binary]`` (v3) needs an explicit driver, so map
    to ``postgresql+psycopg://`` when no ``+driver`` is present. SQLite and
    already-qualified URLs pass through untouched.
    """
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def _create_engine(url: str):
    if url.startswith("sqlite"):
        return create_engine(
            url, future=True, connect_args={"check_same_thread": False}
        )
    # Delegate pooled-endpoint detection to the supabase helper when
    # available (single source of truth for :6543/?pgbouncer); fall back to
    # the local check so a broken helper never breaks engine creation.
    try:
        from backend.db.supabase import create_supabase_engine, is_supabase_pooled

        if is_supabase_pooled(url) or _is_serverless_postgres(url):
            return create_supabase_engine(url)
    except Exception:
        pass
    pooled = _is_serverless_postgres(url)
    url = _normalize_postgres_url(url)
    # psycopg3 rejects ?pgbouncer=true as a libpq option — it is a
    # detection-only flag (see is_supabase_pooled). Strip it before connect.
    if "?" in url:
        base, _, qs = url.partition("?")
        kept = [p for p in qs.split("&") if p and not p.lower().startswith("pgbouncer")]
        url = base + ("?" + "&".join(kept) if kept else "")
    kwargs: dict = {"future": True, "pool_pre_ping": True}
    if pooled:
        kwargs["poolclass"] = NullPool
        # Supabase :6543 is a transaction-mode pooler: named server-side
        # prepared statements do not survive across checkouts
        # (DuplicatePreparedStatement). Disable them; plain queries only.
        kwargs["connect_args"] = {"prepare_threshold": None}
    else:
        kwargs.update(
            pool_size=5, max_overflow=5, pool_timeout=10, pool_recycle=300
        )
    return create_engine(url, **kwargs)


def get_engine(url: str | None = None):
    global _engine
    if url is not None:
        cached = _engines_by_url.get(url)
        if cached is None:
            cached = _create_engine(url)
            _engines_by_url[url] = cached
        return cached
    if _engine is None:
        _engine = _create_engine(database_url())
    return _engine


def reset_engine() -> None:
    """Test hook: drop the cached engine/session factory (env changes)."""
    global _engine, _SessionLocal
    _engine = None
    _SessionLocal = None
    _engines_by_url.clear()
    _session_factories_by_url.clear()


def get_session_factory(url: str | None = None):
    global _SessionLocal
    if url is not None:
        # Normalize empty/whitespace URLs to the default (avoids
        # create_engine("") crashes) and reuse cached factories.
        key = (url or "").strip() or database_url()
        cached = _session_factories_by_url.get(key)
        if cached is None:
            cached = sessionmaker(bind=get_engine(key), autoflush=False, expire_on_commit=False)
            _session_factories_by_url[key] = cached
        return cached
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)
    return _SessionLocal


def init_db(url: str | None = None) -> None:
    Base.metadata.create_all(get_engine(url))


def get_db():
    Session = get_session_factory()
    db = Session()
    try:
        yield db
    finally:
        db.close()
