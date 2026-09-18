"""V2 password hashing: bcrypt wrapper with a fail-closed prod guard.

Preferred backend is ``bcrypt`` (``$2b$`` hashes, per the V2 plan). When
bcrypt is not installed, dev/test fall back to stdlib
PBKDF2-HMAC-SHA256 (``pbkdf2_sha256$...``); production
(``APP_ENV=production|prod``) refuses the fallback with ``RuntimeError``
(fail-closed: never silently downgrade password storage).

Hash strings are self-describing, so :func:`verify_password` dispatches on
the prefix and returns ``False`` (never raises) on mismatch or malformed
input — the login path must 401, not 500.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets

try:  # preferred backend (V2 plan: bcrypt>=4.1)
    import bcrypt as _bcrypt
except Exception:  # pragma: no cover - exercised when bcrypt is absent
    _bcrypt = None  # type: ignore[assignment]

_PBKDF2_ALGO = "sha256"
_PBKDF2_ITERATIONS = 200_000
_PBKDF2_SALT_BYTES = 16
_PBKDF2_PREFIX = "pbkdf2_sha256$"
_BCRYPT_PREFIXES = ("$2a$", "$2b$", "$2y$")
_MAX_PASSWORD_BYTES = 72  # bcrypt input limit; enforced on all backends


def _is_production() -> bool:
    try:
        return (os.getenv("APP_ENV", "") or "").strip().lower() in ("production", "prod")
    except Exception:
        return False


def _backend() -> str:
    return "bcrypt" if _bcrypt is not None else "pbkdf2"


def hash_password(password: str) -> str:
    """Hash ``password``; raises ``ValueError`` on bad input.

    Raises ``RuntimeError`` in production when bcrypt is unavailable
    (fail-closed: refuse to store weaker hashes where it matters).
    """
    if not isinstance(password, str) or not password:
        raise ValueError("password must be a non-empty string")
    raw = password.encode("utf-8")
    if len(raw) > _MAX_PASSWORD_BYTES:
        raise ValueError("password too long (max 72 bytes)")
    if _bcrypt is not None:
        return _bcrypt.hashpw(raw, _bcrypt.gensalt()).decode("utf-8")
    if _is_production():
        raise RuntimeError(
            "bcrypt is required in production: refusing stdlib fallback "
            "for password hashing (install bcrypt>=4.1)"
        )
    salt = secrets.token_bytes(_PBKDF2_SALT_BYTES)
    dk = hashlib.pbkdf2_hmac(_PBKDF2_ALGO, raw, salt, _PBKDF2_ITERATIONS)
    b64 = lambda b: base64.b64encode(b).decode("ascii")  # noqa: E731
    return f"{_PBKDF2_PREFIX}{_PBKDF2_ITERATIONS}${b64(salt)}${b64(dk)}"


def verify_password(password: str, password_hash: str | None) -> bool:
    """True on match; False (never raises) on mismatch/malformed input."""
    try:
        if not isinstance(password, str) or not password:
            return False
        if not isinstance(password_hash, str) or not password_hash:
            return False
        raw = password.encode("utf-8")
        text = password_hash.strip()
        if text.startswith(_BCRYPT_PREFIXES):
            if _bcrypt is None:
                return False  # cannot verify bcrypt without the library
            try:
                return bool(_bcrypt.checkpw(raw, text.encode("utf-8")))
            except Exception:
                return False
        if text.startswith(_PBKDF2_PREFIX):
            parts = text.split("$")
            if len(parts) != 4:
                return False
            try:
                iterations = int(parts[1])
            except ValueError:
                return False
            if iterations <= 0:
                return False
            try:
                salt = base64.b64decode(parts[2])
                expected = base64.b64decode(parts[3])
            except Exception:
                return False
            candidate = hashlib.pbkdf2_hmac(_PBKDF2_ALGO, raw, salt, iterations)
            try:
                return hmac.compare_digest(candidate, expected)
            except Exception:
                return False
        return False  # unknown scheme: fail closed
    except Exception:
        return False


__all__ = ["hash_password", "verify_password"]
