-- OneMarket Analyzer — 0006 revamp (snapshots+compression, indicators cache,
-- AI token ledger, health history, future auth/tiers).
--
-- ADDITIVE-ONLY: no existing table/column is renamed, removed, or retyped.
-- Preserved intact: forecasts.horizon_days IN (1, 7, 14, 21), ai_weight<=0.20,
-- alerts/alert horizon+condition CHECKs, calibration horizon CHECK,
-- audit_logs append-only hash chain (untouched by this file).
--
-- Apply (I apply it; the DB password handling stays with me):
--   psql "$DATABASE_URL" -f infra/migrations/0006_revamp.sql
-- (use the DIRECT (:5432) URL for DDL, not the :6543 pooled URL).
--
-- Idempotent: every statement uses IF NOT EXISTS / re-runnable forms.
-- Safe to apply twice. DO NOT apply to production yourself.
-- Supabase twin: supabase/migrations/0006_revamp.sql (identical DDL + RLS).

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Compressed market-data snapshots (Agent 3 writer contract merged with the
-- revamp readers). UNION of infra/migrations/0006_snapshots.sql (draft) and
-- this file: every writer column (symbol, n_bars, raw_bytes,
-- compressed_bytes, user_id TEXT) is kept verbatim; revamp adds
-- size_raw/size_stored (+tier stub). Append-only: NO UNIQUE constraint
-- (writers plain-INSERT; reads are latest-wins). instrument_id stays
-- nullable lineage-only; user_id stays TEXT (holds UUID strings; no FK).
CREATE TABLE IF NOT EXISTS market_snapshots (
  snapshot_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  symbol           TEXT NOT NULL,
  instrument_id    UUID REFERENCES instruments(instrument_id) ON DELETE CASCADE,
  exchange_mic     TEXT,
  ts               TIMESTAMPTZ NOT NULL DEFAULT now(),
  timeframe        TEXT NOT NULL DEFAULT '1d',
  encoding         TEXT NOT NULL DEFAULT 'gzip+json'
    CHECK (encoding IN ('gzip+json', 'delta-q100+gzip', 'json+gzip',
                        'json', 'zstd', 'raw')),
  payload          BYTEA NOT NULL,
  n_bars           INTEGER NOT NULL DEFAULT 0,
  raw_bytes        INTEGER NOT NULL DEFAULT 0,
  compressed_bytes INTEGER NOT NULL DEFAULT 0,
  size_raw         INTEGER CHECK (size_raw IS NULL OR size_raw >= 0),
  size_stored      INTEGER CHECK (size_stored IS NULL OR size_stored >= 0),
  source           TEXT NOT NULL DEFAULT 'yfinance',
  quality_grade    TEXT NOT NULL DEFAULT 'C'
    CHECK (quality_grade IN ('A', 'B', 'C', 'D')),
  provenance       JSONB NOT NULL DEFAULT '{}'::jsonb,
  user_id          TEXT NULL,   -- hook for future per-user tracking (no FK yet)
  tier             TEXT NULL,   -- stub: free|pro|team|enterprise (no CHECK until auth lands)
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_market_snapshots_symbol_tf_ts
  ON market_snapshots (symbol, timeframe, ts DESC);
CREATE INDEX IF NOT EXISTS ix_market_snapshots_created
  ON market_snapshots (created_at DESC);
CREATE INDEX IF NOT EXISTS ix_market_snapshots_inst_tf_ts
  ON market_snapshots (instrument_id, timeframe, ts DESC);
CREATE INDEX IF NOT EXISTS ix_market_snapshots_ts
  ON market_snapshots (ts DESC);
-- Convergence guards: if 0006_snapshots.sql (draft) applied first, its table
-- lacks these revamp columns — ADD COLUMN converges the schema either order.
ALTER TABLE market_snapshots ADD COLUMN IF NOT EXISTS size_raw INTEGER;
ALTER TABLE market_snapshots ADD COLUMN IF NOT EXISTS size_stored INTEGER;
ALTER TABLE market_snapshots ADD COLUMN IF NOT EXISTS tier TEXT;

-- Per-forecast accuracy (accuracy.py writer contract merged with revamp).
-- UNION of the 0006_snapshots.sql draft and this file: accuracy_id PK and NO
-- FK on forecast_id are load-bearing (scoring covers in-memory records; a
-- hard reference would reject valid rows), so both are kept. Revamp adds
-- realized_ret (mirror of realized_return) + tier stub. CHECKs only encode
-- scorer-guaranteed invariants ((p-y)^2 in [0,1], prob in [0,1], label 0/1).
CREATE TABLE IF NOT EXISTS forecast_accuracy (
  accuracy_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  forecast_id       UUID,              -- no FK: in-memory records score too
  symbol            TEXT NOT NULL,
  exchange_mic      TEXT NOT NULL DEFAULT '',
  horizon_days      INT NOT NULL DEFAULT 21,
  target_date       DATE,
  predicted_prob    NUMERIC(6,5)
    CHECK (predicted_prob IS NULL OR (predicted_prob >= 0 AND predicted_prob <= 1)),
  realized_label    INT CHECK (realized_label IS NULL OR realized_label IN (0, 1)),
  realized_return   NUMERIC(12,8),
  realized_ret      NUMERIC(10,6),     -- revamp mirror of realized_return
  hit               BOOLEAN,           -- (label=1) == (p >= 0.5)
  brier_contrib     NUMERIC(10,8)
    CHECK (brier_contrib IS NULL OR (brier_contrib >= 0 AND brier_contrib <= 1)),
  confidence_before TEXT,
  confidence_after  TEXT,
  model_version     TEXT NOT NULL DEFAULT '',
  data_version      TEXT NOT NULL DEFAULT '',
  user_id           TEXT,              -- hook for future per-user tracking
  tier              TEXT NULL,         -- stub (no CHECK until auth lands)
  scored_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_forecast_accuracy_forecast
  ON forecast_accuracy (forecast_id);
CREATE INDEX IF NOT EXISTS ix_forecast_accuracy_symbol_horizon
  ON forecast_accuracy (symbol, horizon_days, scored_at DESC);
CREATE INDEX IF NOT EXISTS ix_forecast_accuracy_scored
  ON forecast_accuracy (scored_at DESC);
-- Convergence guards (same either-order rationale as market_snapshots).
ALTER TABLE forecast_accuracy ADD COLUMN IF NOT EXISTS realized_ret NUMERIC(10,6);
ALTER TABLE forecast_accuracy ADD COLUMN IF NOT EXISTS tier TEXT;

-- Append-only AI token usage ledger (Agent 4; 0006 successor of the legacy
-- ai_token_logs dataset name). Secrets MUST never be stored here.
CREATE TABLE IF NOT EXISTS ai_token_ledger (
  id            BIGSERIAL PRIMARY KEY,
  provider      TEXT NOT NULL CHECK (provider IN ('gemini', 'openai', 'anthropic', 'xai')),
  model         TEXT NOT NULL DEFAULT '',
  call_type     TEXT NOT NULL DEFAULT 'opinion'
    CHECK (call_type IN ('opinion', 'evidence', 'forecast', 'embedding', 'other')),
  input_tokens  INTEGER NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
  output_tokens INTEGER NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
  latency_ms    INTEGER NULL CHECK (latency_ms IS NULL OR latency_ms >= 0),
  evidence_hash TEXT NULL,
  user_id       UUID NULL,   -- stub -> users.user_id (future third DB; no FK yet)
  tier          TEXT NULL,   -- stub: free|pro|team|enterprise (no CHECK until auth lands)
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_ai_token_ledger_provider_created
  ON ai_token_ledger (provider, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_ai_token_ledger_evidence
  ON ai_token_ledger (evidence_hash) WHERE evidence_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_ai_token_ledger_user_created
  ON ai_token_ledger (user_id, created_at DESC) WHERE user_id IS NOT NULL;

-- Durable provider health samples (Agent 6). The in-memory
-- ProviderHealthTracker stays the live path; this is its durable history.
CREATE TABLE IF NOT EXISTS provider_health_history (
  id         BIGSERIAL PRIMARY KEY,
  provider   TEXT NOT NULL,
  ts         TIMESTAMPTZ NOT NULL DEFAULT now(),
  ok         BOOLEAN NOT NULL,
  latency_ms INTEGER NULL CHECK (latency_ms IS NULL OR latency_ms >= 0),
  error_code TEXT NULL
);
CREATE INDEX IF NOT EXISTS ix_provider_health_history_provider_ts
  ON provider_health_history (provider, ts DESC);
CREATE INDEX IF NOT EXISTS ix_provider_health_history_ts
  ON provider_health_history (ts DESC);

-- Cached deterministic indicator payloads (key e.g. 'rsi-14').
-- Not user-scoped by design: deterministic output is identical every tier.
CREATE TABLE IF NOT EXISTS indicator_cache (
  instrument_id UUID NOT NULL REFERENCES instruments(instrument_id) ON DELETE CASCADE,
  timeframe     TEXT NOT NULL DEFAULT '1d',
  indicator_key TEXT NOT NULL,
  payload       JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (instrument_id, timeframe, indicator_key)
);
CREATE INDEX IF NOT EXISTS ix_indicator_cache_updated
  ON indicator_cache (updated_at DESC);

-- Future auth/tiers stubs on existing user-scopable tables (additive NULL
-- columns only; no backfill, no FK — the users table lands in a third DB
-- later and ORM mapping lands with auth). audit_logs / price_bars /
-- calibration_snapshots / quote_snapshots / provider_* stay global on purpose.
ALTER TABLE alerts   ADD COLUMN IF NOT EXISTS user_id UUID NULL;
ALTER TABLE alerts   ADD COLUMN IF NOT EXISTS tier TEXT NULL;
ALTER TABLE forecasts ADD COLUMN IF NOT EXISTS user_id UUID NULL;
ALTER TABLE forecasts ADD COLUMN IF NOT EXISTS tier TEXT NULL;
CREATE INDEX IF NOT EXISTS ix_alerts_user
  ON alerts (user_id) WHERE user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_forecasts_user_created
  ON forecasts (user_id, created_at DESC) WHERE user_id IS NOT NULL;
