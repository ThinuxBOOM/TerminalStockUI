"""Supabase (pgbouncer-aware) engine helpers.

The serverless agent owns backend/db/session.py — this module is intentionally
separate so both agents can land without merge clashes. Wiring for the
session.py owner (one spot, inside get_engine, before create_engine):

    from backend.db.supabase import create_supabase_engine, is_supabase_pooled

    def get_engine(url: str | None = None):
        ...
        resolved = url or database_url()
        if is_supabase_pooled(resolved):          # :6543 pgbouncer / ?pgbouncer=true
            return create_supabase_engine(resolved)
        return create_engine(resolved, future=True)

Why NullPool: Supabase's :6543 pooler runs in transaction mode, which forbids
server-side prepared statements and makes client-side pooling both redundant
and harmful on serverless (stale/idle connections across frozen instances).
NullPool opens a pooled connection per checkout and closes it on checkin, and
pool_pre_ping=True drops dead connections before use.
"""

from __future__ import annotations

import os
from urllib.parse import parse_qs, urlparse

from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

POOLED_PORT = 6543


def supabase_url() -> str:
    """Effective database URL (Supabase pooled :6543 URL in prod via DATABASE_URL)."""
    return os.getenv("DATABASE_URL", "sqlite:///./onemarket.db")


def is_supabase_pooled(url: str | None = None) -> bool:
    """True when `url` points at a pgbouncer-style pooled endpoint.

    Detects Supabase's transaction-mode pooler by port (:6543) or an explicit
    ?pgbouncer=true query flag. Unparsable/empty URLs return False (never raise
    — session wiring must stay total).
    """
    raw = url if url is not None else supabase_url()
    if not raw:
        return False
    try:
        parsed = urlparse(raw)
    except Exception:
        return False
    try:
        if parsed.port == POOLED_PORT:
            return True
    except ValueError:
        pass  # non-numeric port — fall through to query-flag check
    try:
        flags = {k.lower() for k in parse_qs(parsed.query)}
        return "pgbouncer" in flags
    except Exception:
        return False


def create_supabase_engine(url: str | None = None, **kwargs):
    """Create a pgbouncer-safe SQLAlchemy engine (NullPool + pre_ping).

    No connection is opened here (lazy) — safe to call without network.
    Extra `kwargs` are forwarded to create_engine (e.g. connect_args with
    statement_timeout); poolclass/pool_pre_ping defaults can be overridden.
    """
    resolved = url if url is not None else supabase_url()
    # Strip detection-only ?pgbouncer=true: psycopg3 passes query args to
    # libpq which rejects unknown option "pgbouncer".
    if "?" in resolved:
        base, _, qs = resolved.partition("?")
        kept = [p for p in qs.split("&") if p and not p.lower().startswith("pgbouncer")]
        resolved = base + ("?" + "&".join(kept) if kept else "")
    kwargs.setdefault("poolclass", NullPool)
    kwargs.setdefault("pool_pre_ping", True)
    kwargs.setdefault("future", True)
    return create_engine(resolved, **kwargs)
