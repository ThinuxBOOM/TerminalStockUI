-- OneMarket Analyzer — 0009 ensemble-v3 skill columns (Supabase twin of
-- infra/migrations/0009_ensemble_v3.sql + RLS reassert).
-- Apply on direct :5432 only, never :6543 pooler. Rerun safe.

ALTER TABLE IF EXISTS calibration_snapshots
  ADD COLUMN IF NOT EXISTS calibrated_brier NUMERIC NULL;
ALTER TABLE IF EXISTS calibration_snapshots
  ADD COLUMN IF NOT EXISTS calibrated_ece NUMERIC NULL;
ALTER TABLE IF EXISTS calibration_snapshots
  ADD COLUMN IF NOT EXISTS member_brier JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE IF EXISTS calibration_snapshots
  ADD COLUMN IF NOT EXISTS calibrator JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE IF EXISTS calibration_snapshots ENABLE ROW LEVEL SECURITY;
