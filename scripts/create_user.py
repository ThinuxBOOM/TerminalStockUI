"""Create a user, or reset an existing user's password / admin flag.

    python scripts/create_user.py you@example.com --admin
    python scripts/create_user.py friend@example.com
    python scripts/create_user.py friend@example.com --reset-password

The password is read interactively (never from argv, which lands in shell
history and process listings). Set ONEMARKET_PASSWORD to supply it from a
secret store in automation. Resetting a password or changing the admin flag
revokes that user's existing sessions.

Uses DATABASE_URL, like the backend (inside compose:
``docker compose exec backend python scripts/create_user.py ...``).
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import HTTPException  # noqa: E402
from sqlalchemy import select  # noqa: E402

from backend.api.auth import MIN_PASSWORD_LEN, normalize_email  # noqa: E402
from backend.db.models import User  # noqa: E402
from backend.db.session import ensure_schema, get_session_factory  # noqa: E402
from backend.security.passwords import hash_password  # noqa: E402


def read_password() -> str:
    supplied = os.getenv("ONEMARKET_PASSWORD")
    if supplied is not None:
        return supplied
    first = getpass.getpass("Password: ")
    if first != getpass.getpass("Repeat password: "):
        sys.exit("passwords do not match")
    return first


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("email")
    parser.add_argument("--admin", action="store_true", help="grant admin rights")
    parser.add_argument("--no-admin", action="store_true", help="revoke admin rights")
    parser.add_argument("--reset-password", action="store_true", help="set a new password for an existing user")
    args = parser.parse_args(argv)
    if args.admin and args.no_admin:
        sys.exit("--admin and --no-admin are mutually exclusive")

    try:
        email = normalize_email(args.email)
    except HTTPException:
        sys.exit(f"invalid email: {args.email!r}")

    ensure_schema()
    db = get_session_factory()()
    try:
        user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
        created = user is None
        if created or args.reset_password:
            password = read_password()
            if len(password) < MIN_PASSWORD_LEN:
                sys.exit(f"password must be at least {MIN_PASSWORD_LEN} characters")
            try:
                password_hash = hash_password(password)
            except ValueError as exc:
                sys.exit(str(exc))
        if created:
            user = User(email=email, password_hash=password_hash, is_admin=args.admin, token_version=0)
            db.add(user)
        else:
            if args.reset_password:
                user.password_hash = password_hash
            if args.admin or args.no_admin:
                user.is_admin = bool(args.admin)
            if args.reset_password or args.admin or args.no_admin:
                user.token_version = int(user.token_version or 0) + 1
        db.commit()
        role = "admin" if user.is_admin else "member"
        print(f"{'created' if created else 'updated'} {email} ({role})")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
