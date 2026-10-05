-- OneMarket Analyzer — Supabase migration 0005 (Supabase-ready DDL).
--
-- Derived from infra/migrations/0005_quote_snapshots.sql (identical DDL):
-- last-fetched live quotes, one row per provider symbol. Every live
-- (non-fallback) get_quote upserts it; provider outages then serve the last
-- real market data instead of deterministic placeholders. Stub/fallback
-- quotes are never persisted.
-- Supabase delta vs the base file:
--   * Row Level Security enabled with NO anon/authenticated policies (deny by
--     default; service_role bypasses RLS — see supabase/README.md).
--
-- Apply (pick one; I apply it; the DB password handling stays with me):
--   1. Supabase Dashboard -> SQL editor -> paste this file -> Run; or
--   2. psql "$DATABASE_URL" -f supabase/migrations/0005_quote_snapshots.sql
--      (use the DIRECT (:5432) URL for DDL, not the :6543 pooled URL).
--
-- Idempotent: every statement uses IF NOT EXISTS / re-runnable forms.
-- Safe to apply twice. DO NOT apply to production yourself.

CREATE TABLE IF NOT EXISTS quote_snapshots (
  symbol        TEXT PRIMARY KEY,
  instrument_id UUID REFERENCES instruments(instrument_id) ON DELETE CASCADE,
  exchange_mic  TEXT,
  price         NUMERIC(20,6),
  open          NUMERIC(20,6),
  high          NUMERIC(20,6),
  low           NUMERIC(20,6),
  prev_close    NUMERIC(20,6),
  volume        BIGINT,
  currency      TEXT NOT NULL DEFAULT 'USD',
  change        NUMERIC(20,6),
  change_pct    NUMERIC(10,6),
  source        TEXT NOT NULL DEFAULT 'yfinance',
  as_of         TIMESTAMPTZ NOT NULL DEFAULT now(),
  quality_grade TEXT NOT NULL DEFAULT 'C',
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_quote_snapshots_updated ON quote_snapshots (updated_at DESC);

-- RLS posture: service_role (server-side DATABASE_URL) bypasses RLS and keeps
-- full access. No policies are created for anon/authenticated, so browser keys
-- get zero rows by default. Re-running is safe.
ALTER TABLE quote_snapshots ENABLE ROW LEVEL SECURITY;
