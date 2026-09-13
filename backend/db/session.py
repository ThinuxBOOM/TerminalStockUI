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


def database_url() -> str:
    return os.getenv("DATABASE_URL", "sqlite:///./onemarket.db")


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
    else:
        kwargs.update(
            pool_size=5, max_overflow=5, pool_timeout=10, pool_recycle=300
        )
    return create_engine(url, **kwargs)


def get_engine(url: str | None = None):
    global _engine
    if url is not None:
        return _create_engine(url)
    if _engine is None:
        _engine = _create_engine(database_url())
    return _engine


def reset_engine() -> None:
    """Test hook: drop the cached engine/session factory (env changes)."""
    global _engine, _SessionLocal
    _engine = None
    _SessionLocal = None


def get_session_factory(url: str | None = None):
    global _SessionLocal
    if url is not None:
        return sessionmaker(bind=get_engine(url), autoflush=False, expire_on_commit=False)
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
