"""DB-backed provider secrets + budgets (Phase 3c).

Tables mirror infra/migrations/0004_provider_secrets.sql (Postgres) while
staying SQLite-compatible via portable SQLAlchemy types so unit tests run
with init_db().

- provider_secrets: Fernet ciphertext at rest (same SECRET_KEY-derived key
  as backend.security.secrets); plaintext exists only in function-local
  scope, never logged/returned/audited.
- provider_budgets: per-provider monthly USD caps.

Conventions: UTC updated_at, short-lived sessions closed in finally, reads
never raise (ANY DB issue -> None / False).
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Numeric, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.models import Base

ALLOWED_PROVIDERS = ("gemini", "openai", "anthropic", "xai")
ALLOWED_SET = frozenset(ALLOWED_PROVIDERS)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProviderSecret(Base):
    """Fernet ciphertext for one provider key. Plaintext never persisted."""

    __tablename__ = "provider_secrets"

    provider: Mapped[str] = mapped_column(Text, primary_key=True)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class ProviderBudget(Base):
    """Monthly USD cap for one provider."""

    __tablename__ = "provider_budgets"

    provider: Mapped[str] = mapped_column(Text, primary_key=True)
    monthly_usd: Mapped[float] = mapped_column(Numeric, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


# Aliases: spec calls these "Secret + Budget models".
Secret = ProviderSecret
Budget = ProviderBudget


def _normalize_provider(provider: object) -> str:
    if not isinstance(provider, str):
        return ""
    return provider.strip().lower()


def get_db_secret(provider: str) -> Optional[str]:
    """Return the decrypted provider key, or None on ANY failure.

    Never raises; wrong SECRET_KEY / missing row / missing table all map
    to None so callers treat the provider as unconfigured (never 500).
    """
    try:
        name = _normalize_provider(provider)
        if name not in ALLOWED_SET:
            return None
        from backend.db.session import get_session_factory

        Session = get_session_factory()
        db = Session()
        try:
            row = db.get(ProviderSecret, name)
            if row is None:
                return None
            ciphertext = row.ciphertext
        finally:
            db.close()
        if not isinstance(ciphertext, str) or not ciphertext:
            return None
        from backend.security.secrets import decrypt_secret

        try:
            plaintext = decrypt_secret(ciphertext)
        except Exception:
            return None
        if not isinstance(plaintext, str) or not plaintext.strip():
            return None
        return plaintext
    except Exception:
        return None


def put_db_secret(provider: str, api_key: str, model: str = "") -> None:
    """Encrypt + upsert one provider key. Raises ValueError on bad input."""
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("api_key must be a non-empty string")
    if len(api_key) > 2000:
        raise ValueError("api_key must be <= 2000 characters")
    name = _normalize_provider(provider)
    if name not in ALLOWED_SET:
        raise ValueError(f"unknown provider: {provider!r}")
    if model is None:
        model = ""
    if not isinstance(model, str):
        raise ValueError("model must be a string")
    if len(model) > 64:
        raise ValueError("model must be <= 64 characters")
    from backend.db.session import get_session_factory
    from backend.security.secrets import encrypt_secret

    ciphertext = encrypt_secret(api_key)
    now = _utcnow()
    Session = get_session_factory()
    db = Session()
    try:
        row = db.get(ProviderSecret, name)
        if row is None:
            db.add(ProviderSecret(provider=name, ciphertext=ciphertext, model=model, updated_at=now))
        else:
            row.ciphertext = ciphertext
            row.model = model
            row.updated_at = now
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        raise
    finally:
        db.close()


def get_db_budget(provider: str) -> Optional[float]:
    """Return the stored monthly USD cap, or None when absent/unreadable."""
    try:
        name = _normalize_provider(provider)
        if name not in ALLOWED_SET:
            return None
        from backend.db.session import get_session_factory

        Session = get_session_factory()
        db = Session()
        try:
            row = db.get(ProviderBudget, name)
            if row is None or row.monthly_usd is None:
                return None
            value = float(row.monthly_usd)
        finally:
            db.close()
        if not math.isfinite(value) or value < 0:
            return None
        return value
    except Exception:
        return None


def set_db_budget(provider: str, monthly_usd: float) -> None:
    """Upsert one provider budget. Raises ValueError on bad input."""
    name = _normalize_provider(provider)
    if name not in ALLOWED_SET:
        raise ValueError(f"unknown provider: {provider!r}")
    if isinstance(monthly_usd, bool):
        raise ValueError("monthly_usd must be a number")
    try:
        value = float(monthly_usd)
    except (TypeError, ValueError):
        raise ValueError("monthly_usd must be a number")
    if not math.isfinite(value) or value < 0:
        raise ValueError("monthly_usd must be finite and >= 0")
    from backend.db.session import get_session_factory

    now = _utcnow()
    Session = get_session_factory()
    db = Session()
    try:
        row = db.get(ProviderBudget, name)
        if row is None:
            db.add(ProviderBudget(provider=name, monthly_usd=value, updated_at=now))
        else:
            row.monthly_usd = value
            row.updated_at = now
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        raise
    finally:
        db.close()


def db_configured(provider: str) -> bool:
    """True when a decryptable DB key exists. Never raises."""
    try:
        return get_db_secret(provider) is not None
    except Exception:
        return False
