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
