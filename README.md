# OneMarket Analyzer

Multi-market stock research terminal (NYSE, Nasdaq, Shanghai, Euronext):
quotes and charts with source/freshness on every number, deterministic
analytics, **experimental** direction forecasts, and optional AI opinions
capped at 20% weight. Not investment advice.

Every page and API route requires an account. Stack: FastAPI + React (Vite),
Supabase Postgres, Redis cache, Caddy for HTTPS, all in Docker Compose.

## Deploy on a Linux server

Prerequisites: Docker with the compose plugin, a DNS record for your domain
pointing at the server, ports 80 and 443 open, and a Supabase project.

```bash
git clone <repo> onemarket && cd onemarket
cp .env.example .env          # fill in DOMAIN, SECRET_KEY, CRON_SECRET, DATABASE_URL
docker compose up -d --build
```

Database schema (run once, then after every update that adds a migration):

```bash
# Existing Supabase DB that already has 0001-0010 applied by hand:
docker compose exec backend python scripts/migrate.py baseline 0010
# Then (and on a fresh DB, just this):
docker compose exec backend python scripts/migrate.py up
```

Create your admin account (registration is closed by default):

```bash
docker compose exec backend python scripts/create_user.py you@example.com --admin
```

Services: `web` (Caddy: static app + `/api` proxy + automatic HTTPS),
`backend` (uvicorn, internal only), `redis` (cache), `scheduler` (runs the
data jobs in `deploy/scheduler/crontab`, UTC). Logs: `docker compose logs -f`.

Backups: `scripts/backup.sh` writes a verified `pg_dump` to `./backups/`.

## Develop

```bash
pip install -r backend/requirements-dev.txt
APP_ENV=development uvicorn backend.app:app --reload     # SQLite ./onemarket.db
cd frontend && npm ci && npm run dev                      # proxies /api to :8000
APP_ENV=development python scripts/create_user.py dev@example.com --admin
```

Tests: `python -m pytest backend/tests` (hermetic: temp DB, no network) and
`cd frontend && npm test && npm run build`. CI also applies every migration
to Postgres and runs a Playwright suite against the booted stack
(`docs/TESTING.md`).

## Notes

- Forecast probabilities are experimental. Measured walk-forward over 100
  symbols they are slightly worse than the historical base rate at 1, 7 and
  21 days (`docs/DATA_QUALITY.md`, "Measured skill"). Treat them as a
  research aid, not a signal.
- Market data comes from free/personal-use sources (yfinance, optional
  Alpaca/Finnhub/TwelveData keys). Check their terms before giving other
  people access.
- More: `docs/OPERATIONS.md` (running it), `docs/SECURITY.md`,
  `docs/API_CONTRACT.md`, `docs/USER_GUIDE.md`, `docs/DB_SCHEMA.md`.
