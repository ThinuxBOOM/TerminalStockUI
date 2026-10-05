# Operations

Running OneMarket on a single Linux server with Docker Compose. The
database is Supabase Postgres; everything else runs on the server.

Resources: the backend idles at ~400 MB. The weekly training job (a child
process of the backend container) downloads ten years of S&P 500 history
(~50 MB, cached under `MODEL_CACHE_DIR`) and peaks around 1.5-2 GB of RAM
for ~10 minutes. The daily scoring job takes 1-2 minutes.

## Services

| Service | Image | Reachable from | Role |
|---|---|---|---|
| `web` | `deploy/web.Dockerfile` (Caddy + built SPA) | internet, ports 80/443 | HTTPS (automatic Let's Encrypt), serves the app, proxies `/api/*` and `/health` to the backend |
| `backend` | `backend/Dockerfile` | compose network only | FastAPI (uvicorn, one worker) |
| `redis` | `redis:7-alpine` | compose network only | cache (bounded, not persisted) |
| `scheduler` | `deploy/scheduler/` | compose network only | runs the data jobs below by calling `/api/cron/*` with `CRON_SECRET` |

The backend runs a single worker on purpose: rate limits, in-flight AI jobs
and hot caches live in process memory.

## First deployment

```bash
git clone <repo> onemarket && cd onemarket
cp .env.example .env
# fill in DOMAIN, SECRET_KEY, CRON_SECRET, DATABASE_URL (see .env.example)
docker compose up -d --build
docker compose exec backend python scripts/migrate.py baseline 0010   # only for a DB migrated by hand before
docker compose exec backend python scripts/migrate.py up
docker compose exec backend python scripts/create_user.py you@example.com --admin
```

- DNS: an A/AAAA record for `DOMAIN` must point at the server before the
  first start, or Caddy cannot obtain a certificate.
- Firewall: allow inbound 80 and 443 (TCP, plus 443/UDP for HTTP/3). Nothing
  else needs to be open.
- `DATABASE_URL`: use Supabase's **session pooler** (port 5432) or the direct
  connection. The transaction pooler (6543) works for the app but not for
  migrations (`migrate.py` refuses it).
- A fresh Supabase project also needs the seed once:
  `psql "$DIRECT_URL" -f migrations/seed.sql`.
- Forecasts work straight away with the model bundled in
  `backend/forecasting/v4/default_bundle.json`. The screener fills in after
  the first scoring run, which starts by itself the first time someone opens
  it (or run `docker compose exec scheduler run-job predict`).

The backend refuses to start in production (`APP_ENV` unset or
`production`) when `SECRET_KEY` or `CRON_SECRET` is missing or weak (fewer
than 32 characters or a placeholder) or `DATABASE_URL` is empty. Read the
error in `docker compose logs backend`.

## Updating

```bash
git pull
docker compose up -d --build
docker compose exec backend python scripts/migrate.py up
```

`migrate.py status` lists applied and pending migrations. Migrations that
drop data say so in their header: take a backup first.

| Migration | What it does |
|---|---|
| 0011 | drops the old billing/tier columns (destructive: back up first) |
| 0012 | adds `model_artifacts`, `forecast_scores`, `cross_sections` for forecast engine v4 |
| 0013 | moves benchmark indices (^FCHI, ^AEX, ^BFX) to their own venue, fixes instrument time zones, and de-duplicates any daily bars stamped at 00:00 UTC |

## Accounts

Sign-up is closed unless `ALLOW_REGISTRATION=true`. Manage accounts with:

```bash
docker compose exec backend python scripts/create_user.py friend@example.com
docker compose exec backend python scripts/create_user.py friend@example.com --reset-password
docker compose exec backend python scripts/create_user.py friend@example.com --admin      # or --no-admin
```

Resetting a password or changing the admin flag signs that user out
everywhere. Admins manage AI provider keys and budgets on the Data Health
page; those settings apply to every user.

## Scheduled jobs

`deploy/scheduler/crontab` (UTC). Output goes to `docker compose logs scheduler`.

| Job | Schedule | What it does |
|---|---|---|
| `evaluate` | every 15 min | evaluates alerts |
| `snapshot` | hourly at :07 | stores a compressed daily-bar snapshot per universe symbol |
| `ingest` | 05:30 daily | refreshes daily bars for the default universe |
| `sp500` | 06:00 daily | refreshes S&P 500 daily bars in 10 shards |
| `predict` | 06:30 daily | scores every instrument for every horizon into `forecast_scores` (screener, signals, watchlist ranks) and stores the day's cross-section; runs as a child process |
| `train` | Saturdays 02:00 | retrains forecast engine v4 on ten years of S&P 500 history, re-runs the walk-forward evaluation, stores and activates the new bundle, then rescores; child process |
| `score` | 07:00 daily | scores matured forecasts against realized prices |
| `health` | 08:00 daily | pings each provider and records health |
| `retention` | Sundays 03:00 | purges rows past their retention window |

Run one by hand: `docker compose exec scheduler run-job ingest`. `predict`
and `train` return immediately and log their progress in
`docker compose logs backend`; only one of each runs at a time.

### Forecast model

The active model, its training date and its walk-forward record are on the
Model Lab page and at `GET /api/forecast/model`. Each weekly retrain stores
a new row in `model_artifacts` and activates it; older bundles stay in the
table. To roll back, set `active` on the previous row (and clear it on the
newer one); the backend picks the change up within five minutes.

Train by hand (writes a file, does not touch the database):

```bash
docker compose exec backend python scripts/train_models.py --out /tmp/bundle.json
```

Retention windows are set only through `RETENTION_<DATASET>_DAYS` in `.env`
(defaults in `backend/observability/retention.py`, never below 1 day). The
HTTP endpoint cannot change them.

## Backups and restore

Supabase keeps its own daily backups (plan-dependent). For a copy you
control:

```bash
scripts/backup.sh     # writes backups/onemarket-<UTC stamp>.dump and verifies it is readable
```

Restore into a database (this overwrites matching objects):

```bash
docker run --rm -i -v "$PWD/backups:/backups" postgres:17-alpine \
  pg_restore --clean --if-exists --no-owner -d "$URL" /backups/<file>.dump
```

Redis holds only cache data and needs no backup.

## Secret rotation

| Secret | Effect of rotating |
|---|---|
| `SECRET_KEY` | every user is signed out; AI provider keys stored through the UI can no longer be decrypted and must be re-entered |
| `CRON_SECRET` | none, the backend and scheduler read it from the same `.env` |
| Supabase password | update `DATABASE_URL` |

After editing `.env`: `docker compose up -d` (recreates the affected containers).

## Audit log

`audit_logs` is an append-only hash chain. Verify it with:

```bash
docker compose exec backend python -m backend.observability.audit_verify
```

Exit status 1 means a gap or a rewritten row. The chain is stored in the same
database, so it detects accidental edits, not an attacker with write access.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| backend exits with `SECRET_KEY is missing or weak` / `CRON_SECRET ...` / `DATABASE_URL is required` | set it in `.env` (`openssl rand -hex 32` for secrets), then `docker compose up -d` |
| Caddy logs certificate errors | DNS does not point at this server yet, or port 80 is blocked |
| scheduler logs `FAILED(22) ... 401` | backend and scheduler disagree on `CRON_SECRET`; recreate both with `docker compose up -d` |
| a chart or quote shows "unavailable" | the upstream source returned no live data; the app refuses to show stale data. Retry later or check `docker compose logs backend` |
| `migrate.py` refuses the URL | you used the :6543 transaction pooler; use the session pooler or direct URL |
| `429 rate limit exceeded` | more than `RATE_LIMIT_PER_MIN` API calls (or `AUTH_RATE_LIMIT_PER_MIN` logins, `REFRESH_RATE_LIMIT_PER_MIN` token refreshes) per minute from one address |
| `429 daily AI limit reached` | the user hit `AI_DAILY_CALLS_PER_USER` in the last 24 h |
| screener says it is scoring for the first time | the first `predict` run is in progress (1-2 min); it refreshes by itself |
| `train` ends with exit code 2 | the history download failed (network or Yahoo throttling); the previous model stays active. Rerun later |
| a stock's forecast says it needs 253 daily bars | fewer than a year of daily bars is stored; run `ingest` for it or wait for the nightly job |
