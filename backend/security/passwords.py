"""Password hashing (bcrypt, ``$2b$``).

bcrypt only reads the first 72 bytes of its input, so longer passwords are
rejected instead of being silently truncated. :func:`verify_password` never
raises: malformed input or hashes are a failed login, not a 500.
"""

from __future__ import annotations

import bcrypt

MAX_PASSWORD_BYTES = 72


def hash_password(password: str) -> str:
    """Hash ``password``; raises ``ValueError`` on empty or over-long input."""
    if not isinstance(password, str) or not password:
        raise ValueError("password must be a non-empty string")
    raw = password.encode("utf-8")
    if len(raw) > MAX_PASSWORD_BYTES:
        raise ValueError(f"password too long (max {MAX_PASSWORD_BYTES} bytes)")
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str | None) -> bool:
    if not isinstance(password, str) or not password or not password_hash:
        return False
    raw = password.encode("utf-8")
    if len(raw) > MAX_PASSWORD_BYTES:
        return False
    try:
        return bool(bcrypt.checkpw(raw, password_hash.strip().encode("ascii")))
    except ValueError:
        return False


__all__ = ["MAX_PASSWORD_BYTES", "hash_password", "verify_password"]
