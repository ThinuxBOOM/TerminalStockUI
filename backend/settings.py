"""Process-wide environment settings (single source of truth).

``APP_ENV`` selects the posture. Anything other than ``development`` or
``test`` — including an unset variable — is treated as production, so a
forgotten env var fails closed instead of opening dev-only shortcuts.
"""

from __future__ import annotations

import os

DEV_ENVS = frozenset({"development", "dev", "local", "test"})

#: Minimum length for SECRET_KEY / CRON_SECRET in production.
MIN_SECRET_LEN = 32

_WEAK_MARKERS = ("change-me", "changeme", "test-only", "example", "placeholder")


def app_env() -> str:
    return (os.getenv("APP_ENV", "") or "").strip().lower() or "production"


def is_production() -> bool:
    return app_env() not in DEV_ENVS


def secret_key() -> str:
    """The signing/encryption key. Raises when missing or weak in production.

    Development and tests get a fixed, clearly-named insecure key so local
    runs work without setup; it is never accepted in production.
    """
    raw = (os.getenv("SECRET_KEY", "") or "").strip()
    if is_production():
        if not is_strong_secret(raw):
            raise RuntimeError(
                "SECRET_KEY is missing or weak: set a random value of at least "
                f"{MIN_SECRET_LEN} characters (e.g. `openssl rand -hex 32`)"
            )
        return raw
    return raw or "insecure-development-key-do-not-use-in-production"


def is_strong_secret(value: str | None) -> bool:
    text = (value or "").strip()
    if len(text) < MIN_SECRET_LEN:
        return False
    lowered = text.lower()
    return not any(marker in lowered for marker in _WEAK_MARKERS)


def cron_secret() -> str:
    return (os.getenv("CRON_SECRET", "") or "").strip()


def validate_startup() -> None:
    """Refuse to boot a production process with unsafe configuration."""
    if not is_production():
        return
    secret_key()  # raises when missing/weak
    if not is_strong_secret(cron_secret()):
        raise RuntimeError(
            "CRON_SECRET is missing or weak: set a random value of at least "
            f"{MIN_SECRET_LEN} characters (the scheduler sends it as a Bearer token)"
        )
    if not (os.getenv("DATABASE_URL", "") or "").strip():
        raise RuntimeError("DATABASE_URL is required in production")


__all__ = [
    "DEV_ENVS",
    "MIN_SECRET_LEN",
    "app_env",
    "cron_secret",
    "is_production",
    "is_strong_secret",
    "secret_key",
    "validate_startup",
]
