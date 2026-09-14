-- OneMarket Analyzer — migration 0005: last-fetched live quotes.
--
-- One row per canonical provider symbol (e.g. AAPL, 600519.SS): every live
-- (non-fallback) get_quote upserts it, so a later provider outage serves the
-- last real market data instead of deterministic placeholders. Stub/fallback
-- quotes are never persisted. instrument_id is lineage-only and nullable so
-- symbols outside the registry still get coverage.
--
-- Supabase twin: supabase/migrations/0005_quote_snapshots.sql (identical DDL
-- plus RLS). Apply with psql against the DIRECT (:5432) URL for DDL, never
-- the :6543 pooler. Idempotent: IF NOT EXISTS everywhere; safe to re-apply.

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
