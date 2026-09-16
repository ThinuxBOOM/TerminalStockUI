# OneMarket Analyzer — Operations (v1)

Compose-first (no K8s in v1). Services: `postgres:16`, `redis:7` (AOF persistence),
`backend` (FastAPI :8000), `frontend` (Vite :5173). Full layout: `README.md`.

## 1. Env setup

```powershell
cd "C:\Users\thinu\OneDrive\Pictures\Documents\STOCK ANALYSIS MAIN\onemarket-analyzer"
copy infra\docker\.env.example infra\docker\.env
# edit infra\docker\.env: POSTGRES_PASSWORD + SECRET_KEY at minimum.
#   SECRET_KEY=<base64-urlsafe-32B> (any string works; it is hashed to a Fernet key).
#   AI keys empty by default — the app must run fully AI-disabled.
# never commit infra\docker\.env (gitignored)
```

Key names: `GEMINI_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY / XAI_API_KEY` are
compose-level placeholders. Runtime provider keys live in the encrypted server-side
store (`backend/security/secrets.py`, Fernet over `SECRET_KEY`), set via the Provider
Settings flow — never in code, docs, logs, audit rows, or the browser.

## 2. Bring-up

```powershell
docker compose up --build
docker compose config          # validate without a running daemon
docker compose ps
docker compose logs -f backend # follow one service
docker compose down            # stop; add -v to drop pgdata/redisdata volumes
```

- Frontend: http://localhost:5173 · Backend: http://localhost:8000 (`/health`, `/api/...`)
- Postgres: localhost:5432 · Redis: localhost:6379
- Local dev without compose: see `README.md` (backend venv + `uvicorn`, frontend `npm`).

Apply the schema (until Alembic autogenerate lands; rerun is safe):

```powershell
psql "postgresql://onemarket:<pw>@localhost:5432/onemarket" -f infra\migrations\0001_initial.sql
# — or inside compose:
docker compose exec postgres psql -U onemarket -d onemarket -f /docker-entrypoint-initdb.d/0001_initial.sql
```

## 3. Backup / restore

There is no `infra/scripts/` helper yet — run these `docker compose` commands directly
(they are the normative procedure until a script lands):

```powershell
# postgres backup + restore
docker compose exec postgres pg_dump -U onemarket onemarket | Out-File -Encoding utf8 backup-yyyyMMdd.sql
Get-Content backup-*.sql | docker compose exec -T postgres psql -U onemarket -d onemarket
# redis persistence snapshot (AOF + RDB on demand)
docker compose exec redis redis-cli BGSAVE
docker cp onemarket-redis:/data/appendonlydir ./redis-backup/
```

Backup/restore is covered by backend tests per spec §6.

## 4. Retention

Source: `docs/DATA_QUALITY.md` (M3/M4 appendix extends it for forecasts).

| Data | Retention | Notes |
|---|---|---|
| Raw intraday bars | 2 years | Then downsample to daily, drop raw |
| Daily bars + adjustments | Indefinite | Corporate-action-adjusted; point-in-time |
| Fundamentals filings snapshots | Indefinite | Point-in-time, restatements preserved |
| Forecasts + feature/model/data versions | Indefinite | Calibration dashboards + leakage tests |
| Calibration snapshots (Brier/ECE/reliability) | Indefinite | Tied to the version triple |
| Backtest fold results (incl. failures) | Indefinite | Failures kept visible, not pruned |
| AI opinions + evidence hashes | 3 years | Token usage logged; prompts versioned |
| Audit logs | 7 years, append-only | Hash-chained; backup-tested |
| Redis cache | TTL 5 min – 24 h by endpoint | Provider-health state persistent (AOF) |

## 4b. Scheduled market-data calls, snapshots, and compression lifecycle

Daily bars move once per session, so the schedule is session-aware rather
than frequent. All times UTC.

| What runs | Where | Cadence | Why this time |
|---|---|---|---|
| `GET /api/cron/ingest` (universe bars; Alpaca-first for US when `ALPACA_API_KEY_ID` + `ALPACA_API_SECRET_KEY` resolve, else yfinance/AKShare/Stooq) | Vercel cron | `0 1 * * *` (01:00) | 21:00 ET — after the US close, after Alpaca daily bars finalize; SSE/Euronext long closed |
| `GET /api/cron/calibrate` (walk-forward snapshots) | Vercel cron | `0 2 * * *` (02:00) | After ingest lands, before the EU open |
| `GET /api/cron/evaluate` (alerts) | GH Actions `alerts.yml` | every 15 min | Intraday cadence Vercel Hobby can't host (2-slot cap) |
| `GET /api/cron/snapshot` (compressed 1d snapshot per universe symbol, feed-attributed in `market_snapshots.source`) | GH Actions `snapshots.yml` | hourly (`7 * * * *`) | Bounds replay-point staleness to ~1h for scoring/audits |
| `POST /api/cron/retention` `{"apply": true}` | GH Actions `retention.yml` | weekly Sun 03:00 | Windows are days-to-years wide; weekly keeps DELETE sets small |

Snapshots compress **at capture** (smallest of gzip+json /
delta-q100+gzip / zlib / zstd wins, ~30–60 rows/KB), so no monthly
recompress batch exists by design — the scheduled work is the tiered
lifecycle (`retention.py`, env-overridable via `RETENTION_*_DAYS`):
raw (non-gzip) snapshots 30d → gzip snapshots 1y → 2y backstop; bars 5y;
forecasts/accuracy 3y; audit 7y (head never deleted). Every scheduled
ingest/backfill call also persists one snapshot row for exactly what it
sourced (`source` = winning chain link); snapshot writes are best-effort
and never break ingestion. `GET /api/cron/retention` is always a dry-run
report; deletion needs explicit `{"apply": true}` (same as the local
`python -m backend.observability.retention --apply`).

Alpaca is US-only end to end: quote chain, bar chain (skipped silently
for `.SS/.PA/.AS/.BR`), statements are never asked of it, and the
15-minute bars-failure cooldown keeps one slow view per outage (fast
honest yfinance cover until it lapses).

## 5. Health / audit verification

```powershell
# provider latency/error/circuit dashboard payload
curl http://localhost:8000/api/providers/health
# safe health probe (records a reference quote fetch; never key material)
curl -X POST "http://localhost:8000/api/providers/health/test?provider=yfinance"
# audit hash-chain verification (fails loudly on any gap/rewrite; exit 1)
python -m backend.observability.audit_verify
$env:DATABASE_URL="postgresql+psycopg://onemarket:<pw>@localhost:5432/onemarket"
python -m backend.observability.audit_verify --database-url $env:DATABASE_URL
# backend test slices
python -m pytest backend/tests/test_audit.py backend/tests/test_observability.py -q
python -m pytest backend/tests -q
# v1 definition-of-done checker (this agent)
python scripts/verify_v1.py
python -m py_compile scripts/verify_v1.py
```

Note: `README.md` also mentions `infra/scripts/verify_audit.py` — that path does not
exist; `python -m backend.observability.audit_verify` is the working command (tracked as
a PARTIAL in `docs/V1_CHECKLIST.md`). The default `./onemarket.db` sqlite stub has no
`audit_logs` table; point the verifier at Postgres (above) after applying
`infra/migrations/0001_initial.sql`.

## 6. Secret rotation

- Provider key: overwrite via the settings flow (`put(provider, "api_key", ...)`); old
  ciphertext is discarded. No restart needed.
- `SECRET_KEY`: set a new value in `infra/docker/.env`, restart the backend, then
  **re-store every provider key** — ciphertext from the old key raises
  `ValueError: cannot decrypt secret with current key` by design.
- Never print keys: API `describe()` exposes names only; logs/audit payloads pass
  through `redact_mapping` / `redact_string` (covered by `test_security.py`,
  `test_audit.py`). If a key ever lands in an audit row it cannot be removed
  (append-only hash chain) — rotate the key immediately.

## 7. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `AI request failed` in the UI, forecast intact | No provider key stored (AI-disabled mode is supported: `ai_enabled=False → weight 0`, `backend/ai/blend.py:23-32,87-99`). Profile-label drift is RESOLVED — server normalizes (`backend/api/ai.py:72-76`), so both `Forecast Assist` (sent by `frontend/src/api/client.ts:290-296,666-673`) and `forecast_assist` (`PROFILES` in `backend/ai/prompts/__init__.py:23`) are accepted; unknown profiles 422 with `unknown AI profile` detail. Check 60s AI timeout (`AI_TIMEOUT_MS`, `frontend/src/api/client.ts:664`) vs serverless `maxDuration 60` (`vercel.json:7-11`); see `docs/V1_CHECKLIST.md` item 5 |
| `Cross-market comparison unavailable — FX provenance missing` | Working as designed: FX stale (>24h, `MAX_AGE_HOURS = 24.0`, `backend/market_data/fx/convert.py:24`)/fallback-without-`allow_fallback`/missing (refuse `423 FX_PROVENANCE_MISSING`, `backend/api/fx.py:243-254`); pass `allow_fallback=true` explicitly to rank on fallback (`backend/api/fx.py:68-71,240-242`); see User Guide §5 |
| Forecast/analytics show placeholder or `unavailable` | RESOLVED fetcher drift — client now calls path-style first (`GET /api/forecast/{symbol}`, `GET /api/analytics/{symbol}`, `POST /api/backtest/run`; `frontend/src/api/client.ts:461-476,513-526,577-590`) matching `backend/api/forecast.py:163`, `backend/api/analytics_api.py:159`, `backend/api/backtest.py:243`. If still stale, check backend reachability (`VITE_API_BASE_URL`) or horizon 422 (`backend/api/forecast.py:170-174`) |
| Cron returns `401 {"detail": "unauthorized"}` | `CRON_SECRET` is set but `Authorization: Bearer <secret>` missing/wrong (constant-time compare, `backend/api/cron.py:65-80`); when unset endpoints are open (local dev). Actions workflow sends the header (`../.github/workflows/alerts.yml:33-39`); Vercel crons must also send it |
| Cache stays in-memory despite Redis vars / `redis ... using memory fallback` warnings | `get_cache()` reads `REDIS_URL` or `UPSTASH_REDIS_URL` lazily (`backend/cache.py:87-100`); visible log lines: `REDIS_URL set but redis unavailable (%s); using memory cache` (`backend/cache.py:98`), `redis GET/SET failed, using memory fallback` (`backend/cache.py:67,74`). `/health` reports `"redis": "not-configured"` when `REDIS_URL` unset (`backend/api/health.py:37`). Set `UPSTASH_REDIS_URL` (preferred on Vercel) or `REDIS_URL` |
| Postgres `DuplicatePreparedStatement` / pooled-connection errors on Supabase `:6543` | Transaction-mode pooler can't keep named prepared statements: engine uses `NullPool` + `connect_args = {"prepare_threshold": None}` when URL contains `pgbouncer`/`:6543` or `APP_ENV=production`/`VERCEL=1` (`backend/db/session.py:31-40,70-76`); detection-only `?pgbouncer=true` is stripped pre-connect (`backend/db/session.py:64-69`) |
| `npm` blocked by NVM (`NVM4306`) | Run `nvm reshim` (or `nvm doctor --autofix`), then `npm install` / `npm run typecheck` in `frontend/` |
| `market_state` shows delayed/stale instead of closed | Holiday-calendar stubs (see `docs/SSE_NOTES.md`, `docs/EURONEXT_NOTES.md`); freshness fallback is by design |
