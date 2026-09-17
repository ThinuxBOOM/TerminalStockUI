-- OneMarket Analyzer — 0007 horizons (V2: 1/7/14/21d).
--
-- V1 used horizons (5, 21, 63); V2 uses (1, 7, 14, 21). Fresh installs get
-- the new CHECKs from 0001-0003 directly; this file migrates EXISTING DBs
-- whose unnamed CHECKs still enforce the old set.
--
-- Idempotent + safe to run twice. Drops any horizon_days CHECK on the three
-- tables, then adds a NAMED constraint for the V2 set.
--
-- Apply:
--   psql "$DATABASE_URL" -f infra/migrations/0007_horizons.sql
-- Supabase twin: supabase/migrations/0007_horizons.sql (identical DDL + RLS).

DO $$
DECLARE
  r RECORD;
BEGIN
  FOR r IN
    SELECT conname, conrelid::regclass AS tbl
    FROM pg_constraint
    WHERE contype = 'c'
      AND conrelid::regclass::text IN ('forecasts', 'calibration_snapshots', 'alerts', 'forecast_accuracy', 'public.forecasts', 'public.calibration_snapshots', 'public.alerts')
  LOOP
    BEGIN
      IF pg_get_constraintdef(r.oid) ILIKE '%horizon_days%' THEN
        EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I', r.tbl, r.conname);
      END IF;
    EXCEPTION WHEN OTHERS THEN
      -- best-effort: keep migrating other constraints
      NULL;
    END;
  END LOOP;
END
$$;

-- Generic fallback for default search_path installations (unnamed CHECKs
-- get auto names like forecasts_horizon_days_check — drop if present).
ALTER TABLE IF EXISTS forecasts DROP CONSTRAINT IF EXISTS forecasts_horizon_days_check;
ALTER TABLE IF EXISTS calibration_snapshots DROP CONSTRAINT IF EXISTS calibration_snapshots_horizon_days_check;
ALTER TABLE IF EXISTS alerts DROP CONSTRAINT IF EXISTS alerts_horizon_days_check;
ALTER TABLE IF EXISTS forecast_accuracy DROP CONSTRAINT IF EXISTS forecast_accuracy_horizon_days_check;
ALTER TABLE IF EXISTS forecasts DROP CONSTRAINT IF EXISTS ck_forecasts_horizon_days;
ALTER TABLE IF EXISTS calibration_snapshots DROP CONSTRAINT IF EXISTS ck_calibration_horizon_days;
ALTER TABLE IF EXISTS alerts DROP CONSTRAINT IF EXISTS ck_alerts_horizon_days;

-- V2 named constraints (NOT VALID on huge tables would be nicer, but these
-- tables are small in v1 — validate immediately, fail loudly on bad rows).
ALTER TABLE IF EXISTS forecasts
  ADD CONSTRAINT ck_forecasts_horizon_v2 CHECK (horizon_days IN (1, 7, 14, 21));
ALTER TABLE IF EXISTS calibration_snapshots
  ADD CONSTRAINT ck_calibration_horizon_v2 CHECK (horizon_days IN (1, 7, 14, 21));
ALTER TABLE IF EXISTS alerts
  ADD CONSTRAINT ck_alerts_horizon_v2 CHECK (horizon_days IN (1, 7, 14, 21));
