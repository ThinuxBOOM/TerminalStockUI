-- OneMarket Analyzer — 0004 provider secrets + budgets (Phase 3c).
--
-- Encrypted provider API keys (Fernet ciphertext at rest; never plaintext)
-- + per-provider monthly USD budget caps for the ProviderSettings flow.
-- Keys are written via POST /api/providers/keys and read back only as
-- configuration flags (GET /api/providers/keys/status never returns key
-- material or ciphertext).
--
-- Apply (I apply it; the DB password handling stays with me):
--   psql "$DATABASE_URL" -f infra/migrations/0004_provider_secrets.sql
-- (use the DIRECT (:5432) URL for DDL, not the :6543 pooled URL).
--
-- Idempotent: IF NOT EXISTS / safe re-run. DO NOT apply to production
-- yourself — return the apply note.

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
