"""Supabase migration tests — offline only (no network).

Covers: pooled (:6543) URL detection -> NullPool engine, DDL file contract
(all 4 tables + ai_weight CHECK + Supabase markers), seed instruments.
"""

from __future__ import annotations

from pathlib import Path

from backend.db.supabase import create_supabase_engine, is_supabase_pooled

ROOT = Path(__file__).resolve().parents[2]  # .../onemarket-analyzer
DDL = ROOT / "supabase" / "migrations" / "0001_onemarket.sql"
SEED = ROOT / "supabase" / "seed.sql"

POOLED = "postgresql+psycopg://postgres:secret@db.abcdefgh1234.supabase.co:6543/postgres"
DIRECT = "postgresql+psycopg://postgres:secret@db.abcdefgh1234.supabase.co:5432/postgres"


def test_pooled_url_detected():
    assert is_supabase_pooled(POOLED) is True


def test_direct_url_not_pooled():
    assert is_supabase_pooled(DIRECT) is False


def test_pgbouncer_query_flag_detected():
    assert is_supabase_pooled(DIRECT + "?pgbouncer=true") is True


def test_unparsable_url_never_raises():
    assert is_supabase_pooled("") is False
    assert is_supabase_pooled("not a url at all") is False
    assert is_supabase_pooled("sqlite:///./onemarket.db") is False


def test_pooled_engine_uses_nullpool_no_network():
    psycopg2 = __import__("importlib").import_module("pytest").importorskip("psycopg2")
    assert psycopg2 is not None
    from sqlalchemy.pool import NullPool

    engine = create_supabase_engine(
        "postgresql+psycopg2://postgres:secret@db.abcdefgh1234.supabase.co:6543/postgres"
    )
    assert isinstance(engine.pool, NullPool)
    assert engine.pool._pre_ping is True  # noqa: SLF001 (pool internals, offline assert)
    engine.dispose()  # no connection was ever opened


def test_ddl_contains_all_tables_and_guards():
    text = DDL.read_text(encoding="utf-8")
    lowered = text.lower()
    for table in ("instruments", "price_bars", "forecasts", "audit_logs"):
        assert f"create table if not exists {table}" in lowered
    assert "pgcrypto" in lowered and "uuid-ossp" in lowered
    assert "gen_random_uuid()" in lowered
    assert "timestamptz" in lowered
    assert "provenance" in lowered and "jsonb" in lowered
    assert "prev_hash" in lowered and lowered.count("hash") >= 2  # hash chain cols
    assert "ai_weight" in lowered
    assert "0.20" in text
    assert "check (ai_weight >= 0 and ai_weight <= 0.20)" in lowered
    # required index coverage
    assert "instrument_id" in lowered and "timeframe" in lowered
    assert "ix_price_bars_instrument_timeframe_ts" in lowered
    assert "ix_forecasts_instrument_horizon" in lowered
    assert "enable row level security" in lowered


def test_seed_has_three_smoke_instruments():
    text = SEED.read_text(encoding="utf-8")
    assert "XNAS" in text and "AAPL" in text
    assert "XSHG" in text and "600519" in text
    assert "XPAR" in text and "'MC'" in text
    assert "on conflict" in text.lower()  # idempotent re-seed


def test_uuid_type_is_postgres_native():
    """IDs must survive a Postgres round-trip as uuid.UUID objects.

    Regression: Uuid(native_uuid=False) result-processing assumed strings
    and crashed on psycopg3-native UUIDs (AttributeError on every
    instruments/price_bars read against Supabase; sqlite masked it).
    """
    from backend.db.models import ID_TYPE

    assert type(ID_TYPE).__name__ == "Uuid"
    assert getattr(ID_TYPE, "native_uuid", False) is True


def test_pooled_engine_disables_prepared_statements(monkeypatch):
    """Transaction-mode poolers (:6543) cannot keep named prepared
    statements across checkouts (DuplicatePreparedStatement). Fail-fast
    connects (connect_timeout=5) are also required so a paused Supabase
    fails into "db unavailable" instead of holding the serverless tick
    past Vercel maxDuration (SP500 shard curl exit 28)."""
    from sqlalchemy.pool import NullPool

    import backend.db.session as sess
    import backend.db.supabase as supabase_module

    # Spy at the real call site: the pooled path delegates to
    # supabase.create_supabase_engine, which uses ITS OWN create_engine
    # reference — patching sess.create_engine never fires. Reset the
    # engine cache first so get_engine actually constructs (no stale
    # cached engine from an earlier test short-circuits the spy).
    sess.reset_engine()
    captured: dict = {}
    real_create = supabase_module.create_engine

    def _fake(url, **kwargs):
        captured.clear()
        captured.update(kwargs)
        return real_create(url, **kwargs)

    monkeypatch.setattr(supabase_module, "create_engine", _fake)
    engine = sess.get_engine(POOLED)
    try:
        assert captured.get("poolclass") is NullPool
        assert captured.get("connect_args") == {
            "prepare_threshold": None,
            "connect_timeout": 5,
        }
    finally:
        engine.dispose()
        sess.reset_engine()


def test_direct_engine_keeps_default_prepares(monkeypatch):
    """Direct (:5432) keeps server-side prepares but still fail-fasts
    connects (connect_timeout=5, same SP500 rationale as the pooled
    branch)."""
    import backend.db.session as sess

    sess.reset_engine()
    captured: dict = {}
    real_create = sess.create_engine

    def _fake(url, **kwargs):
        captured.clear()
        captured.update(kwargs)
        return real_create(url, **kwargs)

    monkeypatch.setattr(sess, "create_engine", _fake)
    engine = sess.get_engine(DIRECT)
    try:
        assert "poolclass" not in captured
        assert "prepare_threshold" not in (captured.get("connect_args") or {})
        assert (captured.get("connect_args") or {}).get("connect_timeout") == 5
    finally:
        engine.dispose()
        sess.reset_engine()
