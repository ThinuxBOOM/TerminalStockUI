-- OneMarket Analyzer — 0001 initial schema
-- Spec §4 (canonical instrument) + forecast/audit foundation (Milestone 0, §6–§7).
-- Apply: psql "$DATABASE_URL" -f infra/migrations/0001_initial.sql
-- Idempotent-ish: uses IF NOT EXISTS where Postgres supports it.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- Canonical instrument registry. Never rely solely on a ticker.
CREATE TABLE IF NOT EXISTS instruments (
  instrument_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  exchange_mic    TEXT NOT NULL,          -- XNYS / XNAS / XSHG / XPAR / XAMS / XBRU ...
  exchange_symbol TEXT NOT NULL,          -- e.g. AAPL, 600519.SS, MC.PA
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

-- Deterministic forecasts (source of truth) + bounded AI opinion metadata.
-- Every forecast stores data/feature/model versions + timestamp (no leakage).
CREATE TABLE IF NOT EXISTS forecasts (
  forecast_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  instrument_id     UUID NOT NULL REFERENCES instruments(instrument_id) ON DELETE CASCADE,
  horizon_days      INT NOT NULL CHECK (horizon_days IN (1, 7, 14, 21)),
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
CREATE INDEX IF NOT EXISTS ix_forecasts_instrument_horizon ON forecasts (instrument_id, horizon_days, target_date DESC);

-- Append-only audit log with hash chain for verification (§6: audit log verification).
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
