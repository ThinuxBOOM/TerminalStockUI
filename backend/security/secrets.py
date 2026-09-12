"""Secrets: encrypted-at-rest stub using Fernet.

- Key from SECRET_KEY env (base64 urlsafe 32B) or ephemeral generated key.
- Encrypt on write, decrypt only at call time, never return via API.
- Redaction helpers for logs / audit payloads (tested in test_security.py).
"""

from __future__ import annotations

import base64
import os
import re

from cryptography.fernet import Fernet, InvalidToken

REDACTED = "[REDACTED]"

# Keys that must never appear in plaintext in logs / audit payloads.
_SENSITIVE_KEYS = ("api_key", "apikey", "secret", "token", "password", "authorization", "cookie", "set-cookie")
_SENSITIVE_RE = re.compile(
    r"(?i)(api[_-]?key|secret|token|password|authorization)\s*[:=]\s*['\"]?([^'\"\s,}]+)['\"]?"
)


def _load_key() -> bytes:
    raw = os.getenv("SECRET_KEY", "")
    if raw:
        candidate = raw.encode()
        try:  # accept raw 32 bytes or base64 forms
            if len(base64.urlsafe_b64decode(candidate + b"=" * (-len(candidate) % 4))) == 32:
                return base64.urlsafe_b64encode(
                    base64.urlsafe_b64decode(candidate + b"=" * (-len(candidate) % 4))
                )
        except Exception:
            pass
        digest = __import__("hashlib").sha256(raw.encode()).digest()
        return base64.urlsafe_b64encode(digest)
    return Fernet.generate_key()


_fernet: Fernet | None = None


def get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_load_key())
    return _fernet


def reset_fernet() -> None:  # test hook
    global _fernet
    _fernet = None


def encrypt_secret(plaintext: str) -> str:
    return get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(token: str) -> str:
    try:
        return get_fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("cannot decrypt secret with current key") from exc


class EncryptedSecretStore:
    """Server-side secret store stub: ciphertext at rest, decrypt at call time.

    Swap the dict backend for Vault/KMS later; interface stays identical.
    Keys are namespaced per provider, e.g. ('gemini', 'api_key').
    """

    def __init__(self) -> None:
        self._vault: dict[tuple[str, str], str] = {}

    def put(self, provider: str, name: str, plaintext: str) -> None:
        self._vault[(provider, name)] = encrypt_secret(plaintext)

    def get(self, provider: str, name: str) -> str:
        try:
            return decrypt_secret(self._vault[(provider, name)])
        except KeyError:
            raise KeyError(f"no secret stored for {provider}/{name}") from None

    def delete(self, provider: str, name: str) -> None:
        self._vault.pop((provider, name), None)

    def has(self, provider: str, name: str) -> bool:
        return (provider, name) in self._vault

    def describe(self) -> list[dict]:
        """Safe for API responses: names only, never values."""
        return [{"provider": p, "name": n, "configured": True} for (p, n) in sorted(self._vault)]


def redact_mapping(payload: dict) -> dict:
    """Return a copy with sensitive values replaced by [REDACTED]."""
    clean: dict = {}
    for key, value in (payload or {}).items():
        if isinstance(value, dict):
            clean[key] = redact_mapping(value)
        elif isinstance(value, list):
            clean[key] = [redact_mapping(v) if isinstance(v, dict) else v for v in value]
        elif key.lower() in _SENSITIVE_KEYS or any(s in key.lower() for s in _SENSITIVE_KEYS):
            clean[key] = REDACTED
        else:
            clean[key] = value
    return clean


def redact_string(text: str) -> str:
    return _SENSITIVE_RE.sub(lambda m: f"{m.group(1)}={REDACTED}", text or "")
