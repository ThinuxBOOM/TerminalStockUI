-- OneMarket Analyzer — 0008 users + auth (V2 Phase 1).
--
-- ADDITIVE-ONLY: new users table + indexes only. No existing table/column is
-- renamed, removed, or retyped. Existing user_id/tier stubs (alerts,
-- forecasts, ai_token_ledger, market_snapshots, forecast_accuracy) are left
-- untouched here — FK backfill is deferred (app-layer enforcement only).
--
-- Apply (use the DIRECT (:5432) URL for DDL, never the :6543 pooled URL):
--   psql "$DATABASE_URL" -f infra/migrations/0008_users_auth.sql
-- Supabase twin: supabase/migrations/0008_users_auth.sql (identical DDL + RLS).
--
-- Idempotent: every statement uses IF NOT EXISTS / re-runnable forms.
-- Safe to apply twice (fresh + existing DBs).

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email TEXT NOT NULL UNIQUE,               -- app normalizes lower(trim())
  password_hash TEXT NOT NULL,              -- bcrypt $2b$, never plaintext
  tier TEXT NOT NULL DEFAULT 'free'
    CHECK (tier IN ('free','silver','gold','platinum')),
  stripe_customer_id TEXT NULL UNIQUE,
  stripe_subscription_id TEXT NULL,
  subscription_status TEXT NULL,            -- active/trialing/past_due/canceled/comped
  is_admin BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_users_tier ON users(tier);
CREATE INDEX IF NOT EXISTS ix_users_stripe_customer
  ON users(stripe_customer_id) WHERE stripe_customer_id IS NOT NULL;
