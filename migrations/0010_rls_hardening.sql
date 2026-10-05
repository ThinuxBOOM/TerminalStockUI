-- OneMarket Analyzer — 0010 RLS hardening (all 16 tables).
--
-- Twin: 0010_rls_hardening.sql (supabase/ <-> infra/ — identical DDL).
-- Apply on the DIRECT (:5432) URL only, never the :6543 pooler:
--   psql "$DIRECT_URL" -f <either-twin-path>/0010_rls_hardening.sql
--
-- Posture (see supabase/README.md §4, docs/SECURITY.md):
--   * RLS is ENABLED on all 16 app tables. No policy grants anything to
--     anon/authenticated except users_read_own (SELECT own row on users for
--     authenticated) — browser anon/authenticated keys therefore read ZERO
--     rows everywhere else.
--   * The backend connects with the Postgres owner / service_role credentials
--     via DATABASE_URL, which BYPASS RLS — server-side reads/writes keep full
--     access. The anon key must never appear in backend code/config.
--   * Admin (users.is_admin) is enforced in the APP (backend/auth/guards.py),
--     never as a recursive RLS policy (no is_admin RLS policy by design).
--   * REVOKE ALL ON TABLE ... FROM anon, authenticated removes table-level
--     grants so RLS is the only gate. anon/authenticated are Supabase roles:
--     on vanilla Postgres without those roles the REVOKE lines raise
--     'role does not exist' — safe to skip just the REVOKE lines there;
--     RLS + policy cleanup still apply.
--
-- Idempotent: ALTER TABLE IF EXISTS / DROP POLICY IF EXISTS / guarded DO
-- blocks. Safe to re-run. ADDITIVE-ONLY in effect: tightens grants, removes
-- wide-open policies; never adds a permissive policy. users_read_own is
-- preserved (created if missing when auth.uid() exists, never dropped).

-- ── 1. Force RLS on (re-runnable) ───────────────────────────────────────
ALTER TABLE IF EXISTS instruments            ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS price_bars             ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS forecasts              ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS audit_logs             ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS calibration_snapshots  ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS alerts                 ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS alert_events           ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS provider_secrets       ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS provider_budgets       ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS quote_snapshots        ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS market_snapshots       ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS forecast_accuracy      ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS ai_token_ledger        ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS provider_health_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS indicator_cache        ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS users                  ENABLE ROW LEVEL SECURITY;

-- ── 2. Strip table grants from browser roles ────────────────────────────
REVOKE ALL ON TABLE instruments             FROM anon, authenticated;
REVOKE ALL ON TABLE price_bars              FROM anon, authenticated;
REVOKE ALL ON TABLE forecasts               FROM anon, authenticated;
REVOKE ALL ON TABLE audit_logs              FROM anon, authenticated;
REVOKE ALL ON TABLE calibration_snapshots   FROM anon, authenticated;
REVOKE ALL ON TABLE alerts                  FROM anon, authenticated;
REVOKE ALL ON TABLE alert_events            FROM anon, authenticated;
REVOKE ALL ON TABLE provider_secrets        FROM anon, authenticated;
REVOKE ALL ON TABLE provider_budgets        FROM anon, authenticated;
REVOKE ALL ON TABLE quote_snapshots         FROM anon, authenticated;
REVOKE ALL ON TABLE market_snapshots        FROM anon, authenticated;
REVOKE ALL ON TABLE forecast_accuracy       FROM anon, authenticated;
REVOKE ALL ON TABLE ai_token_ledger         FROM anon, authenticated;
REVOKE ALL ON TABLE provider_health_history FROM anon, authenticated;
REVOKE ALL ON TABLE indicator_cache         FROM anon, authenticated;
REVOKE ALL ON TABLE users                   FROM anon, authenticated;

-- ── 3. Drop legacy wide-open policies if they were ever created ─────────
-- (Each DROP is IF EXISTS → NOTICE, no error, when absent. users_read_own
-- on users is deliberately NOT listed here — it is preserved in §5.)
DROP POLICY IF EXISTS instruments_allow_all ON instruments;
DROP POLICY IF EXISTS instruments_public_read ON instruments;
DROP POLICY IF EXISTS price_bars_allow_all ON price_bars;
DROP POLICY IF EXISTS price_bars_public_read ON price_bars;
DROP POLICY IF EXISTS forecasts_allow_all ON forecasts;
DROP POLICY IF EXISTS forecasts_public_read ON forecasts;
DROP POLICY IF EXISTS audit_logs_allow_all ON audit_logs;
DROP POLICY IF EXISTS audit_logs_public_read ON audit_logs;
DROP POLICY IF EXISTS calibration_snapshots_allow_all ON calibration_snapshots;
DROP POLICY IF EXISTS calibration_snapshots_public_read ON calibration_snapshots;
DROP POLICY IF EXISTS alerts_allow_all ON alerts;
DROP POLICY IF EXISTS alerts_public_read ON alerts;
DROP POLICY IF EXISTS alert_events_allow_all ON alert_events;
DROP POLICY IF EXISTS alert_events_public_read ON alert_events;
DROP POLICY IF EXISTS provider_secrets_allow_all ON provider_secrets;
DROP POLICY IF EXISTS provider_secrets_public_read ON provider_secrets;
DROP POLICY IF EXISTS provider_budgets_allow_all ON provider_budgets;
DROP POLICY IF EXISTS provider_budgets_public_read ON provider_budgets;
DROP POLICY IF EXISTS quote_snapshots_allow_all ON quote_snapshots;
DROP POLICY IF EXISTS quote_snapshots_public_read ON quote_snapshots;
DROP POLICY IF EXISTS market_snapshots_allow_all ON market_snapshots;
DROP POLICY IF EXISTS market_snapshots_public_read ON market_snapshots;
DROP POLICY IF EXISTS forecast_accuracy_allow_all ON forecast_accuracy;
DROP POLICY IF EXISTS forecast_accuracy_public_read ON forecast_accuracy;
DROP POLICY IF EXISTS ai_token_ledger_allow_all ON ai_token_ledger;
DROP POLICY IF EXISTS ai_token_ledger_public_read ON ai_token_ledger;
DROP POLICY IF EXISTS provider_health_history_allow_all ON provider_health_history;
DROP POLICY IF EXISTS provider_health_history_public_read ON provider_health_history;
DROP POLICY IF EXISTS indicator_cache_allow_all ON indicator_cache;
DROP POLICY IF EXISTS indicator_cache_public_read ON indicator_cache;
DROP POLICY IF EXISTS users_allow_all ON users;
DROP POLICY IF EXISTS users_public_read ON users;

-- ── 4. Sweeper: drop ANY residual policy except users_read_own ──────────
-- Catches policies under any other name §3 did not list. Re-runnable.
DO $$
DECLARE r RECORD;
BEGIN
  FOR r IN
    SELECT schemaname, tablename, policyname
    FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename IN ('instruments','price_bars','forecasts','audit_logs',
        'calibration_snapshots','alerts','alert_events','provider_secrets',
        'provider_budgets','quote_snapshots','market_snapshots',
        'forecast_accuracy','ai_token_ledger','provider_health_history',
        'indicator_cache','users')
      AND NOT (tablename = 'users' AND policyname = 'users_read_own')
  LOOP
    BEGIN
      EXECUTE format('DROP POLICY IF EXISTS %I ON %I.%I',
        r.policyname, r.schemaname, r.tablename);
    EXCEPTION WHEN OTHERS THEN NULL;
    END;
  END LOOP;
END
$$;

-- ── 5. Ensure users_read_own exists (authenticated SELECT own row) ──────
-- Kept, never dropped. Skipped on plain Postgres without auth.uid().
DO $$
BEGIN
  IF to_regclass('public.users') IS NULL THEN RETURN; END IF;
  IF to_regprocedure('auth.uid()') IS NULL THEN
    RAISE NOTICE '0010: auth.uid() not present — skipping users_read_own (plain Postgres)';
    RETURN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies
                 WHERE schemaname = 'public' AND tablename = 'users'
                   AND policyname = 'users_read_own') THEN
    CREATE POLICY users_read_own ON public.users
      FOR SELECT TO authenticated USING (auth.uid() = id);
  END IF;
EXCEPTION WHEN undefined_object THEN
  RAISE NOTICE '0010: authenticated role or auth schema missing — skipping users_read_own';
END
$$;
