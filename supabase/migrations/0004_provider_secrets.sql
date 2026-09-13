-- OneMarket Analyzer — Supabase migration 0004 (Supabase-ready DDL).
--
-- Derived from infra/migrations/0004_provider_secrets.sql (identical DDL):
-- encrypted provider API keys (Fernet ciphertext at rest) + per-provider
-- monthly USD budget caps for the ProviderSettings flow.
-- Supabase delta vs the base file:
--   * Row Level Security enabled with NO anon/authenticated policies (deny by
--     default; service_role bypasses RLS — see supabase/README.md).
--
-- Apply (pick one; I apply it; the DB password handling stays with me):
--   1. Supabase Dashboard -> SQL editor -> paste this file -> Run; or
--   2. psql "$DATABASE_URL" -f supabase/migrations/0004_provider_secrets.sql
--      (use the DIRECT (:5432) URL for DDL, not the :6543 pooled URL).
--
-- Idempotent: every statement uses IF NOT EXISTS / re-runnable forms.
-- Safe to apply twice. DO NOT apply to production yourself.

CREATE TABLE IF NOT EXISTS provider_secrets (
  provider    TEXT PRIMARY KEY CHECK (provider IN ('gemini','openai','anthropic','xai')),
  ciphertext  TEXT NOT NULL,
  model       TEXT NOT NULL DEFAULT '',
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS provider_budgets (
  provider     TEXT PRIMARY KEY CHECK (provider IN ('gemini','openai','anthropic','xai')),
  monthly_usd  NUMERIC NOT NULL CHECK (monthly_usd >= 0),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- RLS posture: service_role (server-side DATABASE_URL) bypasses RLS and keeps
-- full access. No policies are created for anon/authenticated, so browser keys
-- get zero rows by default. Re-running is safe.
ALTER TABLE provider_secrets ENABLE ROW LEVEL SECURITY;
ALTER TABLE provider_budgets ENABLE ROW LEVEL SECURITY;
