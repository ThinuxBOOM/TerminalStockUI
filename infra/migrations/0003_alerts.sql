-- OneMarket Analyzer — 0003 alerts (Phase 3b).
--
-- User-defined alert rules + fired-event log. Evaluation runs on the
-- /api/cron/evaluate batch (same CRON_SECRET auth as ingest/calibrate);
-- delivery is a best-effort hook (console default, optional webhook).
-- Email via Resend is explicitly OUT OF SCOPE (follow-up).
--
-- Apply (I apply it; the DB password handling stays with me):
--   psql "$DATABASE_URL" -f infra/migrations/0003_alerts.sql
-- (use the DIRECT (:5432) URL for DDL, not the :6543 pooled URL).
--
-- Idempotent: IF NOT EXISTS / safe re-run. DO NOT apply to production
-- yourself — return the apply note.

CREATE TABLE IF NOT EXISTS alerts (
  alert_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  symbol         TEXT NOT NULL,          -- exchange symbol, e.g. 'AAPL'
  exchange_mic   TEXT NOT NULL DEFAULT '', -- XNYS / XNAS / XSHG / XPAR / XAMS / XBRU ...
  condition      TEXT NOT NULL CHECK (condition IN ('price_above', 'price_below', 'direction_above', 'direction_below', 'change_pct_below')),
  threshold      NUMERIC NOT NULL,
  horizon_days   INT NOT NULL DEFAULT 21 CHECK (horizon_days IN (1, 7, 14, 21)), -- used by direction_*
  target_ccy     TEXT NOT NULL DEFAULT 'USD',
  is_active      BOOLEAN NOT NULL DEFAULT TRUE,
  cooldown_hours INT NOT NULL DEFAULT 24,
  last_fired_at  TIMESTAMPTZ NULL,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_alerts_symbol_active
  ON alerts (symbol, is_active);

CREATE TABLE IF NOT EXISTS alert_events (
  event_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  alert_id   UUID NOT NULL REFERENCES alerts(alert_id) ON DELETE CASCADE,
  symbol     TEXT NOT NULL,
  observed   NUMERIC NOT NULL,
  threshold  NUMERIC NOT NULL,
  provenance JSONB NOT NULL DEFAULT '{}'::jsonb,  -- observation provenance (see docs/API_CONTRACT.md envelope)
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_alert_events_alert
  ON alert_events (alert_id, created_at DESC);
