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
    r"(?i)(api[_-]?key|secret|token|password|authorization|cookie|set-cookie|private)\s*[:=]\s*['\"]?([^'\"\s,}]+)['\"]?"
)


_WEAK_KEY_MARKERS = ("change-me", "test-only", "example", "placeholder", "secret")


def _is_weak_key(raw: str) -> bool:
    try:
        lowered = str(raw or "").strip().lower()
    except Exception:
        return True
    if len(str(raw or "")) < 32:
        return True
    return any(marker in lowered for marker in _WEAK_KEY_MARKERS)


def assert_secret_strength(*, app_env: str | None = None) -> None:
    """Fail loudly in production on weak/ephemeral SECRET_KEY.

    Weak keys previously hashed silently into Fernet (false security) and
    an unset key generated an ephemeral per-process key (restarts invalidate
    all provider_secrets ciphertext). Production refuses both; dev/test
    keep the old behavior for offline runs.
    """
    import os as _os

    env = (app_env or _os.getenv("APP_ENV", "") or "").strip().lower()
    if env not in ("production", "prod"):
        return
    raw = _os.getenv("SECRET_KEY", "") or ""
    if not raw or _is_weak_key(raw):
        raise RuntimeError(
            "SECRET_KEY is missing or weak in production: set a 32+ char "
            "random SECRET_KEY (rotation invalidates stored provider_secrets)"
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
    Bounded to ``MAX_ENTRIES`` keys (oldest-inserted evicted) so long-lived
    processes cannot grow the in-memory vault without bound.
    """

    MAX_ENTRIES = 64

    def __init__(self) -> None:
        self._vault: dict[tuple[str, str], str] = {}

    def put(self, provider: str, name: str, plaintext: str) -> None:
        key = (provider, name)
        # Refresh insertion order on overwrite (deterministic eviction).
        if key in self._vault:
            self._vault.pop(key, None)
        self._vault[key] = encrypt_secret(plaintext)
        while len(self._vault) > self.MAX_ENTRIES:
            try:
                self._vault.pop(next(iter(self._vault)), None)
            except Exception:
                break

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
    """Return a copy with sensitive values replaced by [REDACTED].

    Keys matching the sensitive set (or containing one as a substring)
    are redacted wholesale; string values elsewhere are additionally
    passed through :func:`redact_string` so embedded ``key=value``
    fragments cannot leak via free-text fields.
    """
    if not isinstance(payload, dict):
        return {}
    clean: dict = {}
    for key, value in (payload or {}).items():
        if isinstance(value, dict):
            clean[key] = redact_mapping(value)
        elif isinstance(value, (list, tuple, set)):
            clean[key] = [
                redact_mapping(v) if isinstance(v, dict)
                else (redact_string(v) if isinstance(v, str) else v)
                for v in list(value)
            ]
        elif key.lower() in _SENSITIVE_KEYS or any(s in key.lower() for s in _SENSITIVE_KEYS):
            clean[key] = REDACTED
        elif isinstance(value, str):
            clean[key] = redact_string(value)
        else:
            clean[key] = value
    return clean


def redact_string(text: str) -> str:
    if not isinstance(text, str):
        return text  # type: ignore[return-value]
    return _SENSITIVE_RE.sub(lambda m: f"{m.group(1)}={REDACTED}", text or "")
