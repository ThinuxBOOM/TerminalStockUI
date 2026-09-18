"""Bootstrap (or rotate) the admin user: platinum, comped, is_admin.

Idempotent upsert — safe to re-run (rotation = new hash + rerun). Mirrors
infra/migrations/0008_users_auth.sql::

    INSERT INTO users(email, password_hash, is_admin, tier, subscription_status)
    VALUES (...)
    ON CONFLICT(email) DO UPDATE
      SET password_hash = EXCLUDED.password_hash, is_admin = true,
          tier = 'platinum', subscription_status = 'comped', updated_at = now()

plus an ``admin.bootstrapped`` audit row (actor ``'system'``,
entity_type ``'users'``) whose payload NEVER contains secrets.

Credential precedence (first non-empty wins):
  1. ``ADMIN_PASSWORD_HASH`` env (or ``--password-hash``) — bcrypt string
     generated offline, preferred, never prompts::
         python -c "import bcrypt; print(bcrypt.hashpw(b'<password>', bcrypt.gensalt()).decode())"
  2. ``ADMIN_PASSWORD`` env — hashed in memory only, never stored/logged.
  3. ``getpass`` prompt — hashed in memory only ( twice, must match).

Email precedence: ``--email`` > ``ADMIN_EMAIL`` env. Stored normalized
(lower(trim())).

Why ``comped`` + ``is_admin`` instead of a fake Stripe row: Stripe stays the
source of truth for payers only — no fake ``stripe_subscription_id``, no
webhook spoof, no charge/refund churn. Entitlement = ``if user.is_admin``.

Usage (repo root)::

    python scripts/bootstrap_admin.py [--email a@b.c] [--password-hash '$2b$...']
                                      [--database-url URL]

Database: ``--database-url`` or ``DATABASE_URL`` env (same defaulting as the
backend). Prints a redacted JSON summary (no secrets, no hash). Exit 0 on
success, 2 on config/error.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

ADMIN_TIER = "platinum"
ADMIN_SUBSCRIPTION_STATUS = "comped"
MIN_PASSWORD_LEN = 10  # matches the Phase 2 register rule (>=10 chars)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bootstrap/rotate the admin user (platinum, comped, is_admin)."
    )
    parser.add_argument(
        "--email",
        default="",
        help="Admin email (default: ADMIN_EMAIL env).",
    )
    parser.add_argument(
        "--password-hash",
        default="",
        help="Bcrypt password hash (default: ADMIN_PASSWORD_HASH env).",
    )
    parser.add_argument(
        "--database-url",
        default="",
        help="Database URL (default: DATABASE_URL env).",
    )
    return parser.parse_args(argv)


def _normalize_email(raw: str) -> str:
    return (raw or "").strip().lower()


def _validate_email(email: str) -> Optional[str]:
    """Return an error string, or None when the address looks usable."""
    try:
        from email_validator import validate_email as _validate  # type: ignore

        _validate(email, check_deliverability=False)
        return None
    except ImportError:
        pass  # optional dep: fall back to a minimal shape check below
    except Exception as exc:
        return "invalid email: %s" % exc
    if "@" not in email or "." not in email.split("@")[-1]:
        return "invalid email: must look like name@domain"
    return None


def _looks_like_bcrypt_hash(value: str) -> bool:
    return value.startswith(("$2a$", "$2b$", "$2y$"))


def _hash_password(plaintext: str) -> str:
    try:
        import bcrypt  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "bcrypt is required to hash a password "
            "(pip install -r backend/requirements.txt), or set "
            "ADMIN_PASSWORD_HASH instead"
        ) from exc
    return bcrypt.hashpw(plaintext.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _resolve_password_hash(args: argparse.Namespace) -> str:
    """Preferred: offline-generated hash. Fallback: env/getpass password hashed in memory."""
    given_hash = (args.password_hash or os.getenv("ADMIN_PASSWORD_HASH", "") or "").strip()
    if given_hash:
        if not _looks_like_bcrypt_hash(given_hash):
            raise RuntimeError(
                "ADMIN_PASSWORD_HASH does not look like a bcrypt string "
                "(expected $2a$/$2b$/$2y$ prefix) — refusing to store "
                "what may be a plaintext password as password_hash"
            )
        return given_hash
    password = os.getenv("ADMIN_PASSWORD", "")
    if not password:
        try:
            first = getpass.getpass("Admin password (min %d chars): " % MIN_PASSWORD_LEN)
            second = getpass.getpass("Confirm admin password: ")
        except (EOFError, KeyboardInterrupt) as exc:
            raise RuntimeError("password prompt aborted; set ADMIN_PASSWORD_HASH instead") from exc
        if first != second:
            raise RuntimeError("passwords do not match")
        password = first
    if len(password) < MIN_PASSWORD_LEN:
        raise RuntimeError(
            "password must be at least %d characters" % MIN_PASSWORD_LEN
        )
    hashed = _hash_password(password)
    # Best-effort: drop the plaintext reference as soon as the hash exists.
    del password
    return hashed


def _created_iso(value: Any) -> str:
    """Canonical isoformat for the audit hash material (mirrors backend/api/audit.py).

    SQLite round-trips DateTime(timezone=True) as naive; normalize naive
    timestamps as UTC so the hash material is stable across write/read.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    return str(value)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _append_admin_audit(db: Any, *, user_id: str) -> None:
    """Append admin.bootstrapped(actor='system'). Payload carries no secrets."""
    from backend.db.models import AuditLog

    try:
        from backend.security.secrets import redact_mapping as _redact
    except Exception:  # pragma: no cover - defense in depth only
        _redact = dict  # type: ignore[assignment]

    from sqlalchemy import select

    payload = _redact({
        "tier": ADMIN_TIER,
        "subscription_status": ADMIN_SUBSCRIPTION_STATUS,
        "is_admin": True,
    })
    last = db.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(1)).scalars().first()
    prev_hash = last.hash if last is not None else None
    now = _utcnow()
    digest = AuditLog.compute_hash(
        prev_hash, _created_iso(now), "system", "admin.bootstrapped",
        "users", user_id, payload,
    )
    db.add(AuditLog(
        created_at=now,
        actor="system",
        action="admin.bootstrapped",
        entity_type="users",
        entity_id=user_id,
        payload=payload,
        prev_hash=prev_hash,
        hash=digest,
    ))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        from backend.db.models import Base, User
        from backend.db.session import database_url, get_session_factory, init_db
    except ImportError as exc:
        print(
            json.dumps({
                "ok": False,
                "error": "ImportError: backend.db not importable "
                         "(run from repo root; got %s: %s)" % (type(exc).__name__, exc),
            }),
            file=sys.stderr,
        )
        print(json.dumps({"ok": False, "error": "ImportError"}))
        return 2

    email = _normalize_email(args.email or os.getenv("ADMIN_EMAIL", ""))
    if not email:
        print(
            json.dumps({"ok": False, "error": "missing admin email (set --email or ADMIN_EMAIL)"}),
            file=sys.stderr,
        )
        print(json.dumps({"ok": False, "error": "missing-email"}))
        return 2
    err = _validate_email(email)
    if err is not None:
        print(json.dumps({"ok": False, "error": err}), file=sys.stderr)
        print(json.dumps({"ok": False, "error": "invalid-email"}))
        return 2

    try:
        password_hash = _resolve_password_hash(args)
    except RuntimeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        print(json.dumps({"ok": False, "error": "credential-error"}))
        return 2

    url = (args.database_url or "").strip() or database_url()
    try:
        # Safety net for fresh DBs: create missing tables without touching
        # existing ones. Canonical schema still comes from
        # infra/migrations/0008_users_auth.sql (RLS/policies live there).
        init_db(url)
        SessionLocal = get_session_factory(url)
    except Exception as exc:
        print(
            json.dumps({"ok": False, "error": "DB connect failed: %s: %s"
                        % (type(exc).__name__, exc)}),
            file=sys.stderr,
        )
        print(json.dumps({"ok": False, "error": "db-connect"}))
        return 2

    try:
        from sqlalchemy import select

        with SessionLocal() as db:
            existing = db.execute(select(User).where(User.email == email)).scalars().first()
            now = _utcnow()
            if existing is None:
                user = User(
                    email=email,
                    password_hash=password_hash,
                    tier=ADMIN_TIER,
                    subscription_status=ADMIN_SUBSCRIPTION_STATUS,
                    is_admin=True,
                    created_at=now,
                    updated_at=now,
                )
                db.add(user)
                created = True
            else:
                existing.password_hash = password_hash
                existing.is_admin = True
                existing.tier = ADMIN_TIER
                existing.subscription_status = ADMIN_SUBSCRIPTION_STATUS
                existing.updated_at = now
                user = existing
                created = False
            db.commit()
            db.refresh(user)
            user_id = str(user.id)
            _append_admin_audit(db, user_id=user_id)
            db.commit()
    except Exception as exc:
        print(
            json.dumps({"ok": False, "error": "bootstrap failed: %s: %s"
                        % (type(exc).__name__, exc)}),
            file=sys.stderr,
        )
        print(json.dumps({"ok": False, "error": "bootstrap-failed"}))
        return 2
    finally:
        try:
            del password_hash
        except Exception:
            pass

    # Redacted summary only: never the hash, never a password.
    print(json.dumps({
        "ok": True,
        "email": email,
        "tier": ADMIN_TIER,
        "subscription_status": ADMIN_SUBSCRIPTION_STATUS,
        "is_admin": True,
        "created": created,
    }))
    _ = Base  # keep linter honest about the metadata import
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
