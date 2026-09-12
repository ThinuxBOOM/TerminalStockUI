"""Audit-chain verification: check function + CLI (spec Sec 6: audit log verification).

Chain rule (mirrors backend/db/models.py AuditLog):
    hash = sha256(prev_hash || created_at || actor || action || entity || payload)
    payload serialized as json.dumps(payload, sort_keys=True, default=str)

Usage:
    python -m backend.observability.audit_verify [--database-url URL]
    python backend/observability/audit_verify.py --database-url "sqlite:///./onemarket.db"

Exit 0 when the chain verifies, 1 on any gap/rewrite (fails loudly).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Iterable, Optional, Sequence

try:  # canonical: python -m backend.observability.audit_verify (repo root on path)
    from backend.db.models import AuditLog
except ImportError:  # direct script run: backend/observability/audit_verify.py
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from backend.db.models import AuditLog


def _created_iso(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    return str(value)


def verify_rows(rows: Sequence[Any]) -> dict:
    """Verify an ordered-or-unordered sequence of AuditLog rows.

    Returns {"ok": bool, "checked": int, "failed_id": ..., "error": ...}.
    """
    ordered = sorted(rows, key=lambda r: (r.id is None, r.id))
    prev_hash: Optional[str] = None
    for row in ordered:
        if row.prev_hash != prev_hash:
            return {
                "ok": False,
                "checked": len(ordered),
                "failed_id": getattr(row, "id", None),
                "error": (
                    f"chain gap at id={getattr(row, 'id', '?')}: "
                    f"prev_hash={row.prev_hash!r} != expected {prev_hash!r}"
                ),
            }
        expected = AuditLog.compute_hash(
            row.prev_hash,
            _created_iso(row.created_at),
            row.actor,
            row.action,
            row.entity_type,
            row.entity_id,
            row.payload or {},
        )
        if row.hash != expected:
            return {
                "ok": False,
                "checked": len(ordered),
                "failed_id": getattr(row, "id", None),
                "error": f"hash mismatch at id={getattr(row, 'id', '?')}: stored row was rewritten or tampered",
            }
        prev_hash = row.hash
    return {"ok": True, "checked": len(ordered), "failed_id": None, "error": None}


def verify_chain(db: Any) -> dict:
    """Verify the full audit_logs table via an open SQLAlchemy session."""
    from sqlalchemy import select

    rows = db.execute(select(AuditLog).order_by(AuditLog.id)).scalars().all()
    return verify_rows(rows)


def verify_database(url: str) -> dict:
    """Open a session on url and verify. Used by the CLI and tests."""
    from backend.db.session import get_engine
    from sqlalchemy.orm import sessionmaker

    engine = get_engine(url)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with Session() as db:
        return verify_chain(db)


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the OneMarket audit hash chain.")
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL", "sqlite:///./onemarket.db"),
        help="SQLAlchemy URL (default: $DATABASE_URL or sqlite:///./onemarket.db)",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        result = verify_database(args.database_url)
    except Exception as exc:  # fail loudly on missing DB/tables too
        print(json.dumps({"ok": False, "checked": 0, "failed_id": None, "error": f"{type(exc).__name__}: {exc}"}))
        return 1
    print(json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
