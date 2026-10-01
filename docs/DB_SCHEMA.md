# Database schema

Postgres (Supabase). The schema is defined only by `migrations/NNNN_*.sql`,
applied in order by `scripts/migrate.py`, which records each in
`schema_migrations`. SQLAlchemy models (`backend/db/models.py`,
`backend/security/store_db.py`) mirror it with portable types so the test
suite runs on SQLite; `backend/tests/test_db_session.py` checks the two stay
in step, and CI applies every migration to Postgres.

In production the app never creates or alters tables. In development and
tests, `ensure_schema()` creates missing tables (and adds missing columns to
an old SQLite file).

## Tables

| Table | From | Purpose | Retention |
|---|---|---|---|
| `instruments` | 0001 | canonical registry, identity = (MIC, symbol) | kept |
| `price_bars` | 0001 | daily OHLCV; `ts` is the session's local midnight in UTC | 5 y |
| `forecasts` | 0001 | versioned forecast log (model/feature/data version) | 3 y |
| `audit_logs` | 0001 | append-only hash chain | 7 y, chain head never purged |
| `calibration_snapshots` | 0002, 0009 | walk-forward Brier/ECE per symbol and horizon | kept |
| `alerts` | 0003, 0011 | alert rules, owned by `user_id` | kept |
| `alert_events` | 0003 | fired alerts (cascade with the rule) | kept |
| `provider_secrets` | 0004 | AI provider keys, Fernet ciphertext | kept |
| `provider_budgets` | 0004 | monthly USD cap per AI provider | kept |
| `quote_snapshots` | 0005 | last live quote per symbol | kept |
| `market_snapshots` | 0006 | compressed daily-bar snapshots | 2 y (raw 30 d) |
| `forecast_accuracy` | 0006 | realized outcome per matured forecast | 3 y |
| `ai_token_ledger` | 0006 | one row per AI call (also the source for per-user AI quotas) | 1 y |
| `provider_health_history` | 0006 | provider health samples | 90 d |
| `indicator_cache` | 0006 | cached indicator payloads | 30 d |
| `users` | 0008, 0011 | email, bcrypt hash, `is_admin`, `token_version` | kept |

Retention runs weekly from the scheduler (`backend/observability/retention.py`);
override a window with `RETENTION_<DATASET>_DAYS` in `.env`.

## Relations

```text
instruments 1--* price_bars (instrument_id, timeframe, ts)
instruments 1--* forecasts
users       1--* alerts (user_id, FK ON DELETE CASCADE, added NOT VALID in 0011)
alerts      1--* alert_events (CASCADE)
forecast_accuracy.forecast_id   no FK (in-memory forecasts are scored too)
ai_token_ledger.user_id         nullable (scheduled/system calls)
```

## Constraints worth knowing

- `forecasts.horizon_days`, `alerts.horizon_days` and
  `calibration_snapshots.horizon_days` must be 1, 7, 14 or 21 (0007).
- `forecasts.ai_weight` between 0 and 0.20: AI influence is capped in the
  database as well as in code.
- `provider_secrets` / `provider_budgets` / `ai_token_ledger.provider` must
  be one of gemini, openai, anthropic, xai.
- Row-level security is enabled on every table with no grants to Supabase's
  `anon`/`authenticated` roles (0010). The backend connects as the owner.

## Migration history

| File | Change |
|---|---|
| 0001 | instruments, price_bars, forecasts, audit_logs |
| 0002 | calibration_snapshots |
| 0003 | alerts, alert_events |
| 0004 | provider_secrets, provider_budgets |
| 0005 | quote_snapshots |
| 0006 | market_snapshots, forecast_accuracy, ai_token_ledger, provider_health_history, indicator_cache; user_id columns |
| 0007 | horizon CHECKs moved to 1/7/14/21 |
| 0008 | users |
| 0009 | ensemble-v3 skill columns on calibration_snapshots |
| 0010 | RLS on all tables, grants revoked from anon/authenticated |
| 0011 | billing and tier columns dropped; users.token_version; alerts.user_id FK |

Add a migration as the next number (`0012_short_name.sql`), make it safe to
re-run where possible, and say in its header if it deletes data.
