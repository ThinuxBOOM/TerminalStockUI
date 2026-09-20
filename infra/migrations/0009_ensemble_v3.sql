-- OneMarket Analyzer — 0009 ensemble-v3 skill columns (additive, idempotent).
--
-- Stores per-member Brier, calibrated Brier/ECE and the fitted
-- isotonic/Platt calibrator on calibration_snapshots so live forecasts can
-- use inverse-Brier adaptive weights + OOF-fitted calibration.
-- Apply: psql "$DATABASE_URL" -f infra/migrations/0009_ensemble_v3.sql
-- (DIRECT :5432 URL for DDL, not :6543 pooled). Rerun safe.

ALTER TABLE IF EXISTS calibration_snapshots
  ADD COLUMN IF NOT EXISTS calibrated_brier NUMERIC NULL;
ALTER TABLE IF EXISTS calibration_snapshots
  ADD COLUMN IF NOT EXISTS calibrated_ece NUMERIC NULL;
ALTER TABLE IF EXISTS calibration_snapshots
  ADD COLUMN IF NOT EXISTS member_brier JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE IF EXISTS calibration_snapshots
  ADD COLUMN IF NOT EXISTS calibrator JSONB NOT NULL DEFAULT '{}'::jsonb;
