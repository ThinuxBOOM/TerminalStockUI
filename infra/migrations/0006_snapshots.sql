-- OneMarket Analyzer — migration 0006 addendum (DRAFT, Agent 3): snapshot writer columns.
--
-- Status: DRAFT for Agent 7 (migration owner) to review/apply. Companion to
-- infra/migrations/0006_revamp.sql (applies cleanly BEFORE or AFTER it;
-- every statement is ADD COLUMN / ADD CONSTRAINT IF NOT EXISTS guarded, and
-- duplicate index/constraint names are provoked nowhere).
--
-- Why this file exists alongside 0006_revamp.sql: the revamp DDL carries the
-- instrument-keyed core (UNIQUE (instrument_id, timeframe, ts, encoding),
-- size_raw/size_stored, forecast_accuracy PK on forecast_id). The Agent 3
-- snapshot/scoring writers additionally need the SYMBOL-keyed read path and
-- the confidence-evolution columns below. Union ORM: MarketSnapshot /
-- ForecastAccuracy in backend/db/models.py (single classes; do NOT create a
-- second pair). Applying revamp first then this file yields the union shape;
-- applying this file first is still safe (revamp's IF NOT EXISTS skips the
-- shared objects, and the Postgres DO blocks below skip existing columns).
--
-- Retention contract (see backend/observability/retention.py):
--   raw (non-gzip) snapshots ......... 30 days  (snapshots_raw, created_at)
--   compressed (gzip) snapshots ...... 1 year   (snapshots_compressed, created_at)
--   per-forecast accuracy rows ....... 3 years  (forecast_accuracy, scored_at,
--                                        tied to the forecasts window)
--   aggregate signal ................. forever  (calibration_snapshots
--                                        members["realized"]; no purge rule)

-- Base tables when this file runs FIRST (no-ops when revamp ran first).
CREATE TABLE IF NOT EXISTS market_snapshots (
  snapshot_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  symbol          TEXT NOT NULL DEFAULT '',
  instrument_id   UUID NULL REFERENCES instruments(instrument_id) ON DELETE CASCADE,
  exchange_mic    TEXT,
  ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
  timeframe       TEXT NOT NULL DEFAULT '1d',
  encoding        TEXT NOT NULL DEFAULT 'gzip+json',
  payload         BYTEA NOT NULL,
  n_bars          INT NOT NULL DEFAULT 0,
  raw_bytes       INT NOT NULL DEFAULT 0,
  compressed_bytes INT NOT NULL DEFAULT 0,
  source          TEXT NOT NULL DEFAULT 'yfinance',
  quality_grade   TEXT NOT NULL DEFAULT 'C',
  provenance      JSONB NOT NULL DEFAULT '{}'::jsonb,
  user_id         TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS forecast_accuracy (
  accuracy_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  forecast_id     UUID,
  symbol          TEXT NOT NULL DEFAULT '',
  exchange_mic    TEXT NOT NULL DEFAULT '',
  horizon_days    INT NOT NULL DEFAULT 21,
  target_date     DATE,
  predicted_prob  NUMERIC(6,5),
  realized_label  INT,
  realized_return NUMERIC(12,8),
  hit             BOOLEAN,
  brier_contrib   NUMERIC(10,8),
  confidence_before TEXT,
  confidence_after  TEXT,
  model_version   TEXT NOT NULL DEFAULT '',
  data_version    TEXT NOT NULL DEFAULT '',
  user_id         TEXT,
  scored_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Writer columns when the REVAMP ran first (each guarded; no-ops otherwise).
ALTER TABLE market_snapshots ADD COLUMN IF NOT EXISTS symbol TEXT NOT NULL DEFAULT '';
ALTER TABLE market_snapshots ADD COLUMN IF NOT EXISTS exchange_mic TEXT;
ALTER TABLE market_snapshots ADD COLUMN IF NOT EXISTS n_bars INT NOT NULL DEFAULT 0;
ALTER TABLE market_snapshots ADD COLUMN IF NOT EXISTS raw_bytes INT NOT NULL DEFAULT 0;
ALTER TABLE market_snapshots ADD COLUMN IF NOT EXISTS compressed_bytes INT NOT NULL DEFAULT 0;
ALTER TABLE market_snapshots ADD COLUMN IF NOT EXISTS tier TEXT NULL;

ALTER TABLE forecast_accuracy ADD COLUMN IF NOT EXISTS accuracy_id UUID DEFAULT gen_random_uuid();
ALTER TABLE forecast_accuracy ADD COLUMN IF NOT EXISTS symbol TEXT NOT NULL DEFAULT '';
ALTER TABLE forecast_accuracy ADD COLUMN IF NOT EXISTS exchange_mic TEXT NOT NULL DEFAULT '';
ALTER TABLE forecast_accuracy ADD COLUMN IF NOT EXISTS horizon_days INT NOT NULL DEFAULT 21;
ALTER TABLE forecast_accuracy ADD COLUMN IF NOT EXISTS target_date DATE;
ALTER TABLE forecast_accuracy ADD COLUMN IF NOT EXISTS predicted_prob NUMERIC(6,5);
ALTER TABLE forecast_accuracy ADD COLUMN IF NOT EXISTS realized_label INT;
ALTER TABLE forecast_accuracy ADD COLUMN IF NOT EXISTS realized_return NUMERIC(12,8);
ALTER TABLE forecast_accuracy ADD COLUMN IF NOT EXISTS realized_ret NUMERIC(10,6);
ALTER TABLE forecast_accuracy ADD COLUMN IF NOT EXISTS confidence_before TEXT;
ALTER TABLE forecast_accuracy ADD COLUMN IF NOT EXISTS confidence_after TEXT;
ALTER TABLE forecast_accuracy ADD COLUMN IF NOT EXISTS model_version TEXT NOT NULL DEFAULT '';
ALTER TABLE forecast_accuracy ADD COLUMN IF NOT EXISTS data_version TEXT NOT NULL DEFAULT '';
ALTER TABLE forecast_accuracy ADD COLUMN IF NOT EXISTS user_id TEXT;
ALTER TABLE forecast_accuracy ADD COLUMN IF NOT EXISTS tier TEXT NULL;
ALTER TABLE forecast_accuracy ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();

-- Symbol-keyed read path (writers query by symbol+timeframe; revamp indexes
-- the instrument-keyed path). Safe re-run.
CREATE INDEX IF NOT EXISTS ix_market_snapshots_symbol_tf_ts
  ON market_snapshots (symbol, timeframe, ts DESC);
CREATE INDEX IF NOT EXISTS ix_market_snapshots_created
  ON market_snapshots (created_at DESC);
CREATE INDEX IF NOT EXISTS ix_forecast_accuracy_forecast
  ON forecast_accuracy (forecast_id);
CREATE INDEX IF NOT EXISTS ix_forecast_accuracy_symbol_horizon
  ON forecast_accuracy (symbol, horizon_days, scored_at DESC);
CREATE INDEX IF NOT EXISTS ix_forecast_accuracy_scored
  ON forecast_accuracy (scored_at DESC);

-- Union value domains (superset of the revamp CHECKs; only added when the
-- revamp CHECKs are absent so whichever file runs first wins and the second
-- never conflicts). SQLite ignores these (migration is Postgres-only).
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_market_snapshots_encoding') THEN
    ALTER TABLE market_snapshots ADD CONSTRAINT ck_market_snapshots_encoding
      CHECK (encoding IN ('gzip+json', 'delta-q100+gzip', 'json+gzip', 'json', 'zstd', 'raw'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_forecast_accuracy_brier') THEN
    ALTER TABLE forecast_accuracy ADD CONSTRAINT ck_forecast_accuracy_brier
      CHECK (brier_contrib IS NULL OR (brier_contrib >= 0 AND brier_contrib <= 1));
  END IF;
END $$;
