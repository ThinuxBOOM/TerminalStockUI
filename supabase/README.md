# OneMarket Analyzer — Supabase setup

This folder owns the Supabase Postgres target. Files:

| file | purpose |
|---|---|
| `migrations/0001_onemarket.sql` | Supabase-ready DDL derived from `infra/migrations/0001_initial.sql` (4 tables, CHECKs, indexes, RLS) |
| `migrations/0002_calibration.sql` | calibration snapshots (walk-forward Brier/ECE + reliability) |
| `migrations/0003_alerts.sql` | alert rules + fired-alert events |
| `migrations/0004_provider_secrets.sql` | encrypted provider keys + monthly budget caps |
| `migrations/0005_quote_snapshots.sql` | last-fetched live quotes (outage fallback serves real data, not placeholders) |
| `migrations/0006_health_history.sql` | provider health history + market snapshots + forecast accuracy (score/health crons) |
| `migrations/0007_horizons.sql` | V2 horizon CHECKs (1/7/14/21d) on forecasts/calibration/alerts |
| `migrations/0008_users_auth.sql` | users table (auth + subscription identity) + `users_read_own` SELECT-own policy |
| `migrations/0009_ensemble_v3.sql` | ensemble-v3 skill columns on calibration_snapshots |
| `migrations/0010_rls_hardening.sql` | RLS hardening: ENABLE RLS + REVOKE anon/authenticated on all 16 tables (keeps `users_read_own`) |
| `seed.sql` | registry-parity seeds (30 rows: US/SSE/Euronext + SPY/QQQ/EWQ/EWN/EWK/000001.SS/CAC.PA/IAEX.AS; smoke minimum XNAS-AAPL, XSHG-600519, XPAR-MC) |

## 1. Create the project

1. https://supabase.com/dashboard → New project → note the **database password**.
2. Project Settings → Database → copy two connection strings:
   - **Pooled** (`:6543`, pgbouncer, transaction mode) → app runtime `DATABASE_URL`.
     `postgresql+psycopg://postgres:<password>@db.<ref>.supabase.co:6543/postgres`
   - **Direct** (`:5432`) → DDL/migrations and `psql` only.
     `postgresql://postgres:<password>@db.<ref>.supabase.co:5432/postgres`

## 2. Run migration + seed

Option A — SQL editor (simplest): Dashboard → SQL editor → paste
`migrations/0001_onemarket.sql` → Run → then `0002_calibration.sql` →
`0003_alerts.sql` → `0004_provider_secrets.sql` → `0005_quote_snapshots.sql` →
`0006_health_history.sql` → `0007_horizons.sql` → `0008_users_auth.sql` →
`0009_ensemble_v3.sql` → `0010_rls_hardening.sql` in order → then paste `seed.sql` → Run.
(0001 alone leaves calibration/alerts/snapshots/health tables missing —
`create_all` drift without RLS/CHECKs.)

Option B — psql (use the **direct** `:5432` URL for DDL, never the pooler):

```bash
for f in supabase/migrations/000*.sql; do psql "$DIRECT_URL" -f "$f"; done
psql "$DIRECT_URL" -f supabase/seed.sql
psql "$DIRECT_URL" -c "SELECT exchange_mic, exchange_symbol FROM instruments ORDER BY 1;"
# expect 30 rows (smoke minimum: XNAS/AAPL, XSHG/600519, XPAR/MC)
```

## 3. Vercel env

- `DATABASE_URL` = **pooled** `:6543` URL (SQLAlchemy prefix: `postgresql+psycopg://…:6543/postgres`).
  The backend detects `:6543` and uses `NullPool + pre_ping`
  (`backend/db/supabase.py`) — required for pgbouncer transaction mode.
- `SECRET_KEY` = fresh `openssl rand -hex 32` (never reuse the local `.env.example` placeholder).
- Never set the `anon` key or `service_role` key as backend env — the backend
  connects via `DATABASE_URL` only.

## 4. RLS posture

- `ENABLE ROW LEVEL SECURITY` is on for all 16 tables (see
  `migrations/0010_rls_hardening.sql`): `instruments`, `price_bars`,
  `forecasts`, `audit_logs`, `calibration_snapshots`, `alerts`,
  `alert_events`, `provider_secrets`, `provider_budgets`,
  `quote_snapshots`, `market_snapshots`, `forecast_accuracy`,
  `ai_token_ledger`, `provider_health_history`, `indicator_cache`, `users`.
- **No** policies exist for `anon`/`authenticated` except `users_read_own`
  (SELECT own row on `users`) → browser keys read **zero rows** elsewhere
  by default.
- `service_role` **bypasses RLS** → the server-side app (which connects with the
  Postgres/`DATABASE_URL` credentials, never the anon key) keeps full access.
- Rule: the **anon key must never appear in backend code/config** — server-side
  only ever uses `DATABASE_URL` (+ `SECRET_KEY` for signing).

## 5. Key rotation

1. Dashboard → Project Settings → API → regenerate the leaked key (anon and/or service_role).
2. If the **database password** leaked: Database Settings → Reset database password →
   update `DATABASE_URL` in Vercel (pooled) and redeploy; update any local `.env`.
3. Rotate `SECRET_KEY` the same way (invalidates existing sessions/tokens — expected).
