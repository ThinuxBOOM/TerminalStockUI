"""Database engine + sessions.

``DATABASE_URL`` selects the database (Supabase Postgres in production).
Without it, development and tests fall back to a local SQLite file; a
production process refuses to start instead.

The backend is a long-lived process, so Postgres uses a real connection pool
(``DB_POOL_SIZE``, default 5, plus up to 5 overflow). Supabase's
transaction-mode pooler (port 6543, or a ``?pgbouncer=true`` flag) cannot keep
server-side prepared statements, so they are disabled for those URLs. Prefer
the session-mode pooler or the direct connection for a single server.

Schema is owned by ``supabase/migrations`` (applied with ``scripts/migrate.py``).
:func:`ensure_schema` only creates missing tables in development and tests.
"""

from __future__ import annotations

import os
import threading
from urllib.parse import parse_qs, urlparse

from sqlalchemy import MetaData, create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.schema import CreateTable
from sqlalchemy.orm import sessionmaker

from backend import settings

from .models import Base

POOLED_PORT = 6543

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None
_engines_by_url: dict[str, Engine] = {}
_session_factories_by_url: dict[str, sessionmaker] = {}
_schema_ready: set[str] = set()
_lock = threading.Lock()


def database_url() -> str:
    url = (os.getenv("DATABASE_URL", "") or "").strip()
    if url:
        return url
    if settings.is_production():
        raise RuntimeError("DATABASE_URL is required in production")
    return "sqlite:///./onemarket.db"


def is_transaction_pooler(url: str) -> bool:
    """True for pgbouncer-style transaction poolers (Supabase :6543 / ?pgbouncer=true)."""
    try:
        parsed = urlparse(url or "")
        if parsed.port == POOLED_PORT:
            return True
        return "pgbouncer" in {k.lower() for k in parse_qs(parsed.query)}
    except ValueError:
        return False


def normalize_postgres_url(url: str) -> str:
    """Map bare ``postgres(ql)://`` to the psycopg 3 driver and drop the
    detection-only ``pgbouncer`` query flag (libpq rejects it)."""
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    if "?" in url:
        base, _, qs = url.partition("?")
        kept = [p for p in qs.split("&") if p and not p.lower().startswith("pgbouncer")]
        url = base + ("?" + "&".join(kept) if kept else "")
    return url


def _pool_size() -> int:
    try:
        return max(1, int((os.getenv("DB_POOL_SIZE", "5") or "5").strip()))
    except ValueError:
        return 5


def _create_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        return create_engine(url, future=True, connect_args={"check_same_thread": False})
    connect_args: dict = {"connect_timeout": 5}
    if is_transaction_pooler(url):
        connect_args["prepare_threshold"] = None
    return create_engine(
        normalize_postgres_url(url),
        future=True,
        pool_pre_ping=True,
        pool_size=_pool_size(),
        max_overflow=5,
        pool_timeout=10,
        pool_recycle=300,
        connect_args=connect_args,
    )


def get_engine(url: str | None = None) -> Engine:
    global _engine
    with _lock:
        if url is not None:
            engine = _engines_by_url.get(url)
            if engine is None:
                engine = _engines_by_url[url] = _create_engine(url)
            return engine
        if _engine is None:
            _engine = _create_engine(database_url())
        return _engine


def get_session_factory(url: str | None = None) -> sessionmaker:
    global _SessionLocal
    if url is not None:
        key = url.strip() or database_url()
        factory = _session_factories_by_url.get(key)
        if factory is None:
            factory = sessionmaker(bind=get_engine(key), autoflush=False, expire_on_commit=False)
            _session_factories_by_url[key] = factory
        return factory
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)
    return _SessionLocal


def reset_engine() -> None:
    """Test hook: drop cached engines/session factories (env changes)."""
    global _engine, _SessionLocal
    with _lock:
        for engine in [_engine, *_engines_by_url.values()]:
            if engine is not None:
                engine.dispose()
        _engine = None
        _SessionLocal = None
        _engines_by_url.clear()
        _session_factories_by_url.clear()
        _schema_ready.clear()


def _add_missing_sqlite_columns(engine: Engine) -> None:
    """Keep an old local SQLite file usable after model changes (dev only).

    ``create_all`` never alters existing tables, so new nullable/defaulted
    columns are added here. Postgres schema changes go through migrations.
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            present = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in present:
                    continue
                ddl_type = column.type.compile(dialect=engine.dialect)
                default = getattr(column.default, "arg", None)
                if isinstance(default, bool):
                    clause = f" NOT NULL DEFAULT {int(default)}"
                elif isinstance(default, (int, float)):
                    clause = f" NOT NULL DEFAULT {default}"
                elif isinstance(default, str):
                    clause = " NOT NULL DEFAULT '" + default.replace("'", "''") + "'"
                else:
                    clause = ""  # nullable add
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {ddl_type}{clause}'))


def _drop_legacy_sqlite_columns(engine: Engine) -> None:
    """Rebuild tables that still carry columns the models no longer have (dev only).

    A local SQLite file from before the billing removal still has
    ``users.tier NOT NULL``, which makes every new user insert fail. SQLite
    cannot drop a column used by a CHECK or UNIQUE constraint, so the table
    is rebuilt from the model and the shared columns are copied across.
    Foreign keys are left out of the rebuilt table (SQLite does not enforce
    them unless asked); Postgres goes through migrations instead.
    """
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    stale = []
    for table in Base.metadata.sorted_tables:
        if table.name not in existing:
            continue
        present = [col["name"] for col in inspector.get_columns(table.name)]
        if set(present) - set(table.columns.keys()):
            stale.append((table, [name for name in present if name in table.columns]))
    if not stale:
        return
    with engine.connect() as conn:
        for table, shared in stale:
            tmp = table.to_metadata(MetaData(), name=f"_rebuild_{table.name}")
            cols = ", ".join(f'"{name}"' for name in shared)
            conn.exec_driver_sql(f'DROP TABLE IF EXISTS "{tmp.name}"')
            conn.execute(CreateTable(tmp, include_foreign_key_constraints=[]))
            conn.exec_driver_sql(f'INSERT INTO "{tmp.name}" ({cols}) SELECT {cols} FROM "{table.name}"')
            conn.exec_driver_sql(f'DROP TABLE "{table.name}"')
            conn.exec_driver_sql(f'ALTER TABLE "{tmp.name}" RENAME TO "{table.name}"')
            for index in table.indexes:
                index.create(conn, checkfirst=True)
        conn.commit()


def init_db(url: str | None = None) -> None:
    """Create missing tables (and, on SQLite, fix up columns). Dev/test/scripts only."""
    engine = get_engine(url)
    Base.metadata.create_all(engine)
    if engine.dialect.name == "sqlite":
        # Add first: the rebuild copies every model column, new ones included.
        _add_missing_sqlite_columns(engine)
        _drop_legacy_sqlite_columns(engine)


def ensure_schema(url: str | None = None) -> None:
    """Idempotent, once-per-engine :func:`init_db` outside production.

    Production schema comes only from migrations, so this is a no-op there.
    """
    if settings.is_production():
        return
    key = url or database_url()
    if key in _schema_ready:
        return
    init_db(url)
    _schema_ready.add(key)


def get_db():
    ensure_schema()
    db = get_session_factory()()
    try:
        yield db
    finally:
        db.close()


__all__ = [
    "database_url",
    "ensure_schema",
    "get_db",
    "get_engine",
    "get_session_factory",
    "init_db",
    "is_transaction_pooler",
    "normalize_postgres_url",
    "reset_engine",
]
