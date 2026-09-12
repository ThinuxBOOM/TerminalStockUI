-- OneMarket Analyzer — Supabase migration 0001 (Supabase-ready DDL).
--
-- Derived from infra/migrations/0001_initial.sql (canonical instrument registry
-- + price_bars + forecasts + audit_logs). Supabase deltas vs the base file:
--   * CREATE EXTENSION for pgcrypto AND uuid-ossp (gen_random_uuid source).
--   * gen_random_uuid() PK defaults, TIMESTAMPTZ for ts/as_of/created_at/updated_at.
--   * Explicit idempotent indexes: price_bars (instrument_id, timeframe, ts),
--     forecasts (instrument_id, horizon_days) [forecasts carry no symbol column;
--     instrument_id is the canonical symbol key].
--   * Row Level Security enabled with NO anon/authenticated policies (deny by
--     default; service_role bypasses RLS — see supabase/README.md).
--
-- Apply (pick one):
--   1. Supabase Dashboard -> SQL editor -> paste this file -> Run, then run
--      supabase/seed.sql the same way; or
--   2. psql "$DATABASE_URL" -f supabase/migrations/0001_onemarket.sql
--      (use the DIRECT (:5432) URL for DDL, not the :6543 pooled URL).
--
-- Idempotent: every statement uses IF NOT EXISTS / ON CONFLICT-safe forms or
-- plain ALTERs that are safe to re-run. Safe to apply twice.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- Canonical instrument registry. Never rely solely on a ticker.
CREATE TABLE IF NOT EXISTS instruments (
  instrument_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  exchange_mic    TEXT NOT NULL,          -- XNYS / XNAS / XSHG / XPAR / XAMS / XBRU ...
  exchange_symbol TEXT NOT NULL,          -- e.g. AAPL, 600519, MC
  provider_symbol TEXT,                   -- raw symbol at upstream provider
  isin            TEXT,
  company_name    TEXT NOT NULL,
  currency        CHAR(3) NOT NULL,       -- ISO 4217
  country         CHAR(2),                -- ISO 3166-1 alpha-2
  sector          TEXT,
  timezone        TEXT NOT NULL DEFAULT 'UTC',
  trading_calendar TEXT NOT NULL DEFAULT 'XNYS',
  is_active       BOOLEAN NOT NULL DEFAULT TRUE,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_instruments_mic_symbol UNIQUE (exchange_mic, exchange_symbol)
);
CREATE INDEX IF NOT EXISTS ix_instruments_symbol_trgm ON instruments USING gin (exchange_symbol gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_instruments_company_trgm ON instruments USING gin (company_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_instruments_isin ON instruments (isin) WHERE isin IS NOT NULL;

-- OHLCV bars, corporate-action adjusted upstream; one row per (instrument, timeframe, ts).
CREATE TABLE IF NOT EXISTS price_bars (
  instrument_id UUID NOT NULL REFERENCES instruments(instrument_id) ON DELETE CASCADE,
  ts            TIMESTAMPTZ NOT NULL,     -- bar open time, UTC
  timeframe     TEXT NOT NULL DEFAULT '1d',
  open          NUMERIC(20,6),
  high          NUMERIC(20,6),
  low           NUMERIC(20,6),
  close         NUMERIC(20,6),
  volume        BIGINT,
  source        TEXT NOT NULL,            -- e.g. yfinance / akshare
  as_of         TIMESTAMPTZ NOT NULL,     -- when upstream said the data is current as of
  quality_grade TEXT NOT NULL DEFAULT 'C',-- A/B/C/D per docs/DATA_QUALITY.md
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (instrument_id, timeframe, ts)
);
CREATE INDEX IF NOT EXISTS ix_price_bars_ts ON price_bars (ts DESC);
-- Hot query path: latest bars per instrument+timeframe (PK already covers it;
-- explicit index documents the Supabase/PostgREST access pattern).
CREATE INDEX IF NOT EXISTS ix_price_bars_instrument_timeframe_ts
  ON price_bars (instrument_id, timeframe, ts DESC);

-- Deterministic forecasts (source of truth) + bounded AI opinion metadata.
-- ai_weight is hard-capped at 0.20: AI is an opinion overlay, never the driver.
CREATE TABLE IF NOT EXISTS forecasts (
  forecast_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  instrument_id     UUID NOT NULL REFERENCES instruments(instrument_id) ON DELETE CASCADE,
  horizon_days      INT NOT NULL CHECK (horizon_days IN (5, 21, 63)),
  target_date       DATE NOT NULL,
  direction_prob    NUMERIC(6,5) CHECK (direction_prob >= 0 AND direction_prob <= 1),
  expected_ret_low  NUMERIC(10,6),
  expected_ret_high NUMERIC(10,6),
  volatility_regime TEXT CHECK (volatility_regime IN ('low','normal','elevated','extreme')),
  drawdown_prob     NUMERIC(6,5) CHECK (drawdown_prob IS NULL OR (drawdown_prob >= 0 AND drawdown_prob <= 1)),
  confidence        TEXT CHECK (confidence IN ('low','moderate','high')),
  model_version     TEXT NOT NULL,        -- versioned together: model + features + prompts + AI schemas
  feature_version   TEXT NOT NULL,
  data_version      TEXT NOT NULL,
  ai_provider       TEXT,                 -- NULL when AI disabled
  ai_model          TEXT,
  ai_weight         NUMERIC(4,3) NOT NULL DEFAULT 0 CHECK (ai_weight >= 0 AND ai_weight <= 0.20),
  provenance        JSONB NOT NULL DEFAULT '{}'::jsonb,  -- see docs/API_CONTRACT.md envelope
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_forecast_run UNIQUE (instrument_id, horizon_days, target_date, model_version, feature_version, data_version)
);
-- Symbol/horizon lookup: instrument_id is the canonical symbol key (no raw
-- ticker column on forecasts by design — join instruments for display symbols).
CREATE INDEX IF NOT EXISTS ix_forecasts_instrument_horizon ON forecasts (instrument_id, horizon_days, target_date DESC);

-- Append-only audit log with hash chain for verification.
CREATE TABLE IF NOT EXISTS audit_logs (
  id          BIGSERIAL PRIMARY KEY,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  actor       TEXT NOT NULL,              -- system / user:<id> / worker:<name>
  action      TEXT NOT NULL,              -- forecast.created, ai.opinion.requested, provider.circuit_open, ...
  entity_type TEXT NOT NULL,
  entity_id   TEXT NOT NULL,
  payload     JSONB NOT NULL DEFAULT '{}'::jsonb,  -- secrets MUST be redacted before insert
  prev_hash   TEXT,
  hash        TEXT NOT NULL               -- sha256(prev_hash || created_at || actor || action || entity || payload)
);
CREATE INDEX IF NOT EXISTS ix_audit_logs_entity ON audit_logs (entity_type, entity_id, id DESC);
CREATE INDEX IF NOT EXISTS ix_audit_logs_created ON audit_logs (created_at DESC);

-- RLS posture: service_role (server-side DATABASE_URL) bypasses RLS and keeps
-- full access. No policies are created for anon/authenticated, so browser keys
-- get zero rows on every table by default. Re-running is safe.
ALTER TABLE instruments ENABLE ROW LEVEL SECURITY;
ALTER TABLE price_bars  ENABLE ROW LEVEL SECURITY;
ALTER TABLE forecasts   ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs  ENABLE ROW LEVEL SECURITY;
