-- OneMarket Analyzer — 0012: forecast engine v4 (trained models + daily scores).
--
-- Apply with `python scripts/migrate.py`. Idempotent; adds tables only.
--
-- model_artifacts   one row per trained v4 bundle (JSON coefficients and the
--                   walk-forward report); `active` marks the one in use.
-- forecast_scores   latest forecast per (symbol, horizon), written by the
--                   daily `predict` job so the screener and overview are
--                   instant. Overwritten daily; history lives in `forecasts`.
-- cross_sections    one day's universe distribution (quantile grids, sector
--                   medians) used to rank a single symbol against its peers.

CREATE TABLE IF NOT EXISTS model_artifacts (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  engine      TEXT NOT NULL,
  version     TEXT NOT NULL UNIQUE,
  bundle      JSONB NOT NULL,
  active      BOOLEAN NOT NULL DEFAULT FALSE,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_model_artifacts_active ON model_artifacts (engine, active, created_at DESC);

CREATE TABLE IF NOT EXISTS forecast_scores (
  symbol         TEXT NOT NULL,
  horizon_days   INTEGER NOT NULL CHECK (horizon_days IN (1, 7, 14, 21)),
  exchange_mic   TEXT NOT NULL,
  as_of          DATE NOT NULL,
  model_version  TEXT NOT NULL,
  last_close     NUMERIC,
  p_up           NUMERIC(6, 5),
  p_out          NUMERIC(6, 5),
  out_rank       NUMERIC(6, 5),
  sigma          NUMERIC(10, 6),
  q10            NUMERIC(10, 6),
  q50            NUMERIC(10, 6),
  q90            NUMERIC(10, 6),
  drawdown_prob  NUMERIC(6, 5),
  vol_regime     TEXT,
  payload        JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (symbol, horizon_days)
);
CREATE INDEX IF NOT EXISTS ix_forecast_scores_rank ON forecast_scores (horizon_days, out_rank DESC);

CREATE TABLE IF NOT EXISTS cross_sections (
  as_of          DATE PRIMARY KEY,
  model_version  TEXT NOT NULL,
  data           JSONB NOT NULL,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE model_artifacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE forecast_scores ENABLE ROW LEVEL SECURITY;
ALTER TABLE cross_sections  ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON model_artifacts, forecast_scores, cross_sections FROM anon, authenticated;
