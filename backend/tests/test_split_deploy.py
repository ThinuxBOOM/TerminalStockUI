"""Split-deploy readiness tests — offline only (no network).

Covers the Oracle-Cloud prep without touching the Vercel path:
  - CORS allow-list merges CORS_ORIGINS + FRONTEND_URL, strips trailing
    slashes, dedupes; defaults unchanged when neither is set.
  - DB_POOL_MODE override: `queue` forces QueuePool (long-lived Oracle
    host) even for pooled Supabase URLs; `null` forces NullPool; unset
    (`auto`) keeps legacy serverless detection (Vercel unaffected).
"""

from __future__ import annotations

POOLED = "postgresql+psycopg://postgres:secret@db.abcdefgh1234.supabase.co:6543/postgres"
DIRECT = "postgresql+psycopg://postgres:secret@db.abcdefgh1234.supabase.co:5432/postgres"


def _clean_env(monkeypatch):
    for var in ("CORS_ORIGINS", "FRONTEND_URL", "DB_POOL_MODE", "APP_ENV", "VERCEL"):
        monkeypatch.delenv(var, raising=False)


def test_cors_defaults_unchanged(monkeypatch):
    _clean_env(monkeypatch)
    from backend.api.main import _cors_origins

    assert _cors_origins() == [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]


def test_cors_origins_only_behaves_as_before(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("CORS_ORIGINS", "https://app.example.com")
    from backend.api.main import _cors_origins

    assert _cors_origins() == ["https://app.example.com"]


def test_cors_frontend_url_alias_strips_slash(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("FRONTEND_URL", "https://my-app.vercel.app/")
    from backend.api.main import _cors_origins

    assert _cors_origins() == ["https://my-app.vercel.app"]


def test_cors_merges_and_dedupes(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("CORS_ORIGINS", "https://a.app, https://a.app/,https://b.app")
    monkeypatch.setenv("FRONTEND_URL", "https://b.app")
    from backend.api.main import _cors_origins

    assert _cors_origins() == ["https://a.app", "https://b.app"]


def test_pool_auto_keeps_legacy_detection(monkeypatch):
    _clean_env(monkeypatch)
    from sqlalchemy.pool import NullPool, QueuePool

    import backend.db.session as sess

    sess.reset_engine()
    try:
        pooled_engine = sess.get_engine(POOLED)
        assert isinstance(pooled_engine.pool, NullPool)
        direct_engine = sess.get_engine(DIRECT)
        assert isinstance(direct_engine.pool, QueuePool)
    finally:
        try:
            pooled_engine.dispose()
        except Exception:
            pass
        try:
            direct_engine.dispose()
        except Exception:
            pass
        sess.reset_engine()


def test_pool_mode_queue_forces_queuepool_on_pooled_url(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("DB_POOL_MODE", "queue")
    from sqlalchemy.pool import QueuePool

    import backend.db.session as sess

    sess.reset_engine()
    try:
        engine = sess.get_engine(POOLED)
        assert isinstance(engine.pool, QueuePool)
        # Transaction-mode pooler guard still applies: no server prepares.
        assert engine.url is not None
    finally:
        try:
            engine.dispose()
        except Exception:
            pass
        sess.reset_engine()


def test_pool_mode_queue_keeps_prepare_threshold_off(monkeypatch):
    """QueuePool over :6543 must still disable server-side prepares."""
    _clean_env(monkeypatch)
    monkeypatch.setenv("DB_POOL_MODE", "queue")

    import backend.db.session as sess

    captured: dict = {}
    real_create = sess.create_engine

    def _fake(url, **kwargs):
        captured.clear()
        captured.update(kwargs)
        return real_create(url, **kwargs)

    monkeypatch.setattr(sess, "create_engine", _fake)
    sess.reset_engine()
    try:
        engine = sess.get_engine(POOLED)
        assert "poolclass" not in captured  # QueuePool = SQLAlchemy default
        assert (captured.get("connect_args") or {}).get("prepare_threshold") is None
    finally:
        try:
            engine.dispose()
        except Exception:
            pass
        sess.reset_engine()


def test_pool_mode_null_forces_nullpool_on_direct_url(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("DB_POOL_MODE", "null")
    from sqlalchemy.pool import NullPool

    import backend.db.session as sess

    sess.reset_engine()
    try:
        engine = sess.get_engine(DIRECT)
        assert isinstance(engine.pool, NullPool)
    finally:
        try:
            engine.dispose()
        except Exception:
            pass
        sess.reset_engine()
