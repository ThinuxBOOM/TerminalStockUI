# Testing

## Backend (pytest)

```bash
pip install -r backend/requirements-dev.txt
python -m pytest backend/tests -q
```

`backend/tests/conftest.py` makes every run hermetic: a throwaway SQLite
database (never `./onemarket.db` or Supabase), no provider or AI keys from
your shell, and outbound network blocked (loopback only). Set
`ALLOW_NETWORK_TESTS=1` to allow network for ad-hoc live checks.

Most suites replace the JWT dependency with a fixed admin
(`backend/tests/auth_helpers.py`); `test_auth.py` exercises the real token
path, including a sweep that every data route rejects anonymous requests.

Notable coverage:

| Area | Files |
|---|---|
| auth, revocation, admin gates, per-user alerts | `test_auth.py`, `test_hardening.py` |
| cron auth and retention floor | `test_cron_retention.py`, `test_cron_timeouts.py` |
| fail-closed data (502/423/422, never stale 200) | `test_failure_modes.py`, `test_provenance.py`, `test_honesty_envelope.py` |
| session dates east of UTC, quote parsing | `test_session_dates.py`, `test_sse_provider.py` |
| forecast engine v4: no look-ahead, scale-free features, range calibration, JSON round trip, end-to-end training on synthetic data, serving, API, daily scoring, screener, signals, public summary | `test_forecast_v4.py` |
| risk metrics (vol, beta, drawdown, VaR) and the risk API | `test_risk.py` |
| bar timestamps at exchange midnight, index venues | `test_forecast_v4.py`, `test_alpaca_bars.py` |
| key redaction, audit hash chain | `test_security_redaction.py`, `test_audit.py` |
| migrations vs models | `test_db_session.py` |

## Frontend (vitest)

```bash
cd frontend
npm ci
npm test          # logic specs plus server-rendered component tests (charts, forecast panels)
npm run build
```

## Browser end-to-end (Playwright)

Runs against a live deployment with a real account:

```bash
cd frontend
npx playwright install chromium        # once
E2E_BASE_URL=http://localhost E2E_EMAIL=you@example.com E2E_PASSWORD=... npm run e2e
```

The specs cover the login wall, a failed login, and sign in, security brief,
reload (session restore), sign out (revocation). Without `E2E_EMAIL` and
`E2E_PASSWORD` they skip. The journey reads live market data.

## Migrations

CI applies every migration to Postgres 16 (with `deploy/ci/supabase_shim.sql`
standing in for Supabase's roles), re-runs them to prove the runner is
idempotent, and loads the seed. Locally:

```bash
docker run -d --name pg -e POSTGRES_PASSWORD=pw -p 55432:5432 postgres:16-alpine
docker exec -i pg psql -U postgres < deploy/ci/supabase_shim.sql
MIGRATE_DATABASE_URL=postgresql://postgres:pw@localhost:55432/postgres python scripts/migrate.py up
```

## CI

`.github/workflows/ci.yml`: backend tests, frontend tests and build,
migrations against Postgres, and `stack-e2e`, which builds the production
images, boots the compose stack, migrates, creates a user and runs the
Playwright suite. Every job must pass; none are advisory.
