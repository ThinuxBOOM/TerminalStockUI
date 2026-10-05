-- OneMarket Analyzer — Supabase migration 0008 (Supabase-ready DDL).
--
-- Derived from infra/migrations/0008_users_auth.sql (identical DDL):
-- users table (auth + subscription identity) + tier / stripe-customer indexes.
-- Supabase delta vs the base file:
--   * Row Level Security enabled with a single SELECT-own policy for
--     authenticated users. No INSERT/UPDATE/DELETE policies for
--     anon/authenticated — all writes go through the backend DATABASE_URL
--     owner creds (existing bypass pattern; service_role bypasses RLS).
--     No is_admin RLS policy (admin is enforced in the app, never recursive
--     in RLS).
--
-- Apply (pick one; use the DIRECT (:5432) URL for DDL, never :6543 pooler):
--   1. Supabase Dashboard -> SQL editor -> paste this file -> Run; or
--   2. psql "$DATABASE_URL" -f supabase/migrations/0008_users_auth.sql
--
-- Idempotent: every statement uses IF NOT EXISTS / re-runnable forms.
-- Safe to apply twice (fresh + existing DBs). ADDITIVE-ONLY: no existing
-- table/column renamed or removed; stub FK backfill deferred (app-layer only).

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

-- RLS posture: service_role (server-side DATABASE_URL) bypasses RLS and keeps
-- full access. Authenticated users may read only their own row (auth.uid()
-- matches the PK); anon reads zero rows. Re-running is safe.
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS users_read_own ON users;
CREATE POLICY users_read_own ON users FOR SELECT TO authenticated USING (auth.uid() = id);
