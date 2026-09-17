-- OneMarket Analyzer — 0002 calibration snapshots (Phase 2b).
--
-- Walk-forward calibration snapshots per (symbol, horizon, model/feature/data
-- version): Brier score, ECE, reliability table + per-member hit rates.
-- The reader side (GET /api/forecast `calibration` rows) consumes the latest
-- snapshot per (symbol, horizon_days, model_version).
--
-- Apply (I apply it; the DB password handling stays with me):
--   psql "$DATABASE_URL" -f infra/migrations/0002_calibration.sql
-- (use the DIRECT (:5432) URL for DDL, not the :6543 pooled URL).
--
-- Idempotent: IF NOT EXISTS / safe re-run. DO NOT apply to production
-- yourself — return the apply note.

CREATE TABLE IF NOT EXISTS calibration_snapshots (
  snapshot_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  model_version TEXT NOT NULL,
  feature_version TEXT NOT NULL,
  horizon_days  INT NOT NULL CHECK (horizon_days IN (1, 7, 14, 21)),
  symbol        TEXT NOT NULL,          -- exchange symbol, e.g. 'AAPL'
  exchange_mic  TEXT NOT NULL,          -- XNYS / XNAS / XSHG / XPAR / XAMS / XBRU ...
  brier         NUMERIC NULL,           -- mean((p - y)^2); NULL when n_windows = 0
  ece           NUMERIC NULL,           -- expected calibration error; NULL when n_windows = 0
  n_windows     INT NOT NULL DEFAULT 0,
  reliability   JSONB NOT NULL DEFAULT '[]'::jsonb,  -- [{bin_low, bin_high, count, mean_predicted, fraction_positive}]
  members       JSONB NOT NULL DEFAULT '{}'::jsonb,  -- {member_name: {hit_rate: float|null, n: int}}
  data_version  TEXT NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_calibration_snapshot UNIQUE (symbol, horizon_days, model_version, feature_version, data_version)
);
CREATE INDEX IF NOT EXISTS ix_calibration_snapshots_symbol_horizon
  ON calibration_snapshots (symbol, horizon_days, created_at DESC);
