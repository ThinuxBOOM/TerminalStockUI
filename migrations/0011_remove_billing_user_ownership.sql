-- OneMarket Analyzer — 0011: remove billing/tiers, per-user alerts, token revocation.
--
-- Apply with `python scripts/migrate.py` (records it in schema_migrations).
-- Uses the DIRECT or session-pooler connection, never the :6543 transaction
-- pooler. Idempotent: safe to re-run.
--
-- Destructive: drops the Stripe/tier columns from users and the unused
-- `tier` stubs elsewhere. Take a backup first (scripts/backup.sh).

-- 1. users: billing identity out, token revocation in ------------------------
ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 0;
DROP INDEX IF EXISTS ix_users_tier;
DROP INDEX IF EXISTS ix_users_stripe_customer;
ALTER TABLE users
  DROP COLUMN IF EXISTS tier,
  DROP COLUMN IF EXISTS stripe_customer_id,
  DROP COLUMN IF EXISTS stripe_subscription_id,
  DROP COLUMN IF EXISTS subscription_status;

-- 2. unused tier stubs (added in 0006, never read) ----------------------------
ALTER TABLE alerts            DROP COLUMN IF EXISTS tier;
ALTER TABLE forecasts         DROP COLUMN IF EXISTS tier;
ALTER TABLE market_snapshots  DROP COLUMN IF EXISTS tier;
ALTER TABLE forecast_accuracy DROP COLUMN IF EXISTS tier;
ALTER TABLE ai_token_ledger   DROP COLUMN IF EXISTS tier;

-- 3. alerts are owned by a user ------------------------------------------------
-- alerts.user_id (UUID NULL) exists since 0006. Rows created before ownership
-- stay NULL and are invisible to every user; delete them if you like:
--   DELETE FROM alerts WHERE user_id IS NULL;
-- NOT VALID skips checking historical rows; new rows are enforced.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_alerts_user') THEN
    ALTER TABLE alerts
      ADD CONSTRAINT fk_alerts_user FOREIGN KEY (user_id)
      REFERENCES users(id) ON DELETE CASCADE NOT VALID;
  END IF;
END $$;
CREATE INDEX IF NOT EXISTS ix_alerts_user ON alerts (user_id) WHERE user_id IS NOT NULL;
