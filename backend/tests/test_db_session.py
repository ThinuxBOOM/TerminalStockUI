"""DB session wiring (offline): URL handling, pool settings, dev-only schema."""

from __future__ import annotations

from pathlib import Path

import pytest

import backend.db.session as sess
from backend.db.session import is_transaction_pooler, normalize_postgres_url

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "migrations" / "seed.sql"

POOLED = "postgresql+psycopg://postgres:secret@db.abcdefgh1234.supabase.co:6543/postgres"
DIRECT = "postgresql+psycopg://postgres:secret@db.abcdefgh1234.supabase.co:5432/postgres"


def test_transaction_pooler_detection():
    assert is_transaction_pooler(POOLED) is True
    assert is_transaction_pooler(DIRECT) is False
    assert is_transaction_pooler(DIRECT + "?pgbouncer=true") is True
    assert is_transaction_pooler("") is False
    assert is_transaction_pooler("sqlite:///./onemarket.db") is False


def test_url_normalization():
    assert normalize_postgres_url("postgres://u:p@h:5432/d").startswith("postgresql+psycopg://")
    assert normalize_postgres_url("postgresql://u:p@h/d").startswith("postgresql+psycopg://")
    assert normalize_postgres_url(DIRECT + "?pgbouncer=true&sslmode=require").endswith("?sslmode=require")


@pytest.mark.parametrize("url,prepares_disabled", [(POOLED, True), (DIRECT, False)])
def test_postgres_engine_uses_queue_pool(monkeypatch, url, prepares_disabled):
    captured: dict = {}

    class _FakeEngine:
        def dispose(self) -> None:
            pass

    def _fake_create_engine(u, **kw):
        captured.update(kw)
        return _FakeEngine()

    monkeypatch.setattr(sess, "create_engine", _fake_create_engine)
    sess.reset_engine()
    try:
        sess.get_engine(url)
    finally:
        sess.reset_engine()
    assert captured["pool_pre_ping"] is True
    assert captured["pool_size"] >= 1
    assert "poolclass" not in captured  # long-lived server: real pooling
    assert captured["connect_args"]["connect_timeout"] == 5
    assert ("prepare_threshold" in captured["connect_args"]) is prepares_disabled


def test_production_requires_database_url(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError):
        sess.database_url()


def test_ensure_schema_is_noop_in_production(monkeypatch):
    called = []
    monkeypatch.setattr(sess, "init_db", lambda url=None: called.append(url))
    monkeypatch.setenv("APP_ENV", "production")
    sess.ensure_schema("sqlite:///:memory:")
    assert called == []


def test_sqlite_missing_columns_are_added(tmp_path):
    from sqlalchemy import create_engine, inspect, text

    url = f"sqlite:///{(tmp_path / 'old.db').as_posix()}"
    old = create_engine(url)
    with old.begin() as conn:
        conn.execute(text(
            "CREATE TABLE users (id CHAR(32) PRIMARY KEY, email TEXT NOT NULL, "
            "password_hash TEXT NOT NULL, is_admin BOOLEAN NOT NULL)"
        ))
    old.dispose()
    sess.reset_engine()
    try:
        sess.init_db(url)
        cols = {c["name"] for c in inspect(sess.get_engine(url)).get_columns("users")}
    finally:
        sess.reset_engine()
    assert "token_version" in cols


def test_uuid_type_is_postgres_native():
    from backend.db.models import ID_TYPE

    assert type(ID_TYPE).__name__ == "Uuid"
    assert getattr(ID_TYPE, "native_uuid", False) is True


def test_seed_has_three_smoke_instruments():
    text = SEED.read_text(encoding="utf-8")
    assert "XNAS" in text and "AAPL" in text
    assert "XSHG" in text and "600519" in text
    assert "XPAR" in text and "'MC'" in text
    assert "on conflict" in text.lower()


def test_migration_files_are_numbered_and_unique():
    import re as _re

    names = sorted(p.name for p in (ROOT / "migrations").glob("*.sql") if p.name != "seed.sql")
    versions = [n[:4] for n in names]
    assert all(_re.match(r"^\d{4}_[a-z0-9_]+\.sql$", n) for n in names)
    assert len(versions) == len(set(versions))


def test_billing_removal_migration_matches_models():
    from backend.db.models import User

    sql = (ROOT / "migrations" / "0011_remove_billing_user_ownership.sql").read_text().lower()
    for column in ("tier", "stripe_customer_id", "stripe_subscription_id", "subscription_status"):
        assert f"drop column if exists {column}" in sql
        assert column not in User.__table__.columns
    assert "token_version" in sql and "token_version" in User.__table__.columns
    assert "fk_alerts_user" in sql
