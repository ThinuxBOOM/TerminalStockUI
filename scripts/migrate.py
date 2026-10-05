"""Apply ``migrations/*.sql`` to Postgres, recording each in ``schema_migrations``.

Usage (repo root):

    python scripts/migrate.py status              # what is applied / pending
    python scripts/migrate.py up                  # apply pending migrations
    python scripts/migrate.py baseline 0010       # mark 0001..0010 as applied
                                                  # (databases migrated by hand)

The database comes from ``MIGRATE_DATABASE_URL`` or else ``DATABASE_URL``.
Use Supabase's direct connection or session pooler for DDL, never the
transaction pooler on port 6543.

Each file runs in its own transaction; a failure rolls that file back and
stops. ``seed.sql`` is not a migration: load it once with
``psql "$URL" -f migrations/seed.sql``.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = ROOT / "migrations"
_VERSION_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")

sys.path.insert(0, str(ROOT))
from backend.db.session import is_transaction_pooler, normalize_postgres_url  # noqa: E402


def migration_files() -> list[tuple[str, Path]]:
    out = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        match = _VERSION_RE.match(path.name)
        if match:
            out.append((match.group(1), path))
    return out


def _engine():
    url = (os.getenv("MIGRATE_DATABASE_URL") or os.getenv("DATABASE_URL") or "").strip()
    if not url:
        sys.exit("set MIGRATE_DATABASE_URL (or DATABASE_URL) to the Postgres connection string")
    if not url.startswith(("postgres://", "postgresql")):
        sys.exit("migrations target Postgres only (the SQL uses Postgres features)")
    if is_transaction_pooler(url):
        sys.exit("use the direct or session-pooler URL for migrations, not the :6543 transaction pooler")
    return create_engine(normalize_postgres_url(url), future=True)


def _applied(conn) -> set[str]:
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version TEXT PRIMARY KEY,"
        " filename TEXT NOT NULL,"
        " applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
    ))
    return {row[0] for row in conn.execute(text("SELECT version FROM schema_migrations"))}


def cmd_status(engine) -> int:
    with engine.begin() as conn:
        applied = _applied(conn)
    for version, path in migration_files():
        print(f"{'applied' if version in applied else 'PENDING':8} {path.name}")
    return 0


def cmd_up(engine) -> int:
    with engine.begin() as conn:
        applied = _applied(conn)
    pending = [(v, p) for v, p in migration_files() if v not in applied]
    if not pending:
        print("up to date")
        return 0
    for version, path in pending:
        print(f"applying {path.name} ...", flush=True)
        sql = path.read_text(encoding="utf-8")
        try:
            with engine.begin() as conn:
                # Raw driver call without parameters: SQL files contain literal
                # '%' (e.g. ILIKE patterns) that must not be read as placeholders.
                conn.connection.driver_connection.execute(sql)
                conn.execute(
                    text("INSERT INTO schema_migrations (version, filename) VALUES (:v, :f)"),
                    {"v": version, "f": path.name},
                )
        except Exception as exc:  # report the failing file, not a stack trace
            detail = str(getattr(exc, "orig", exc)).strip().splitlines()[0]
            print(f"FAILED {path.name}: {detail}", file=sys.stderr)
            print("the file was rolled back; later migrations were not attempted", file=sys.stderr)
            return 1
    print(f"applied {len(pending)} migration(s)")
    return 0


def cmd_baseline(engine, upto: str) -> int:
    files = [(v, p) for v, p in migration_files() if v <= upto]
    if not files:
        sys.exit(f"no migrations at or below {upto}")
    with engine.begin() as conn:
        applied = _applied(conn)
        for version, path in files:
            if version not in applied:
                conn.execute(
                    text("INSERT INTO schema_migrations (version, filename) VALUES (:v, :f)"),
                    {"v": version, "f": path.name},
                )
                print(f"marked {path.name} as applied (not executed)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("up")
    base = sub.add_parser("baseline")
    base.add_argument("version", help="highest version already applied, e.g. 0010")
    args = parser.parse_args(argv)
    engine = _engine()
    if args.command == "status":
        return cmd_status(engine)
    if args.command == "up":
        return cmd_up(engine)
    return cmd_baseline(engine, args.version)


if __name__ == "__main__":
    raise SystemExit(main())
