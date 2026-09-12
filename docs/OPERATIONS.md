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
| `AI request failed` in the UI, forecast intact | No provider key stored (AI-disabled mode) or UI profile-label drift (`Forecast Assist` vs backend key `forecast_assist`) — call `/api/ai/insight` with the snake_case key; see `docs/V1_CHECKLIST.md` |
| `Cross-market comparison unavailable — FX provenance missing` | Working as designed: FX stale/fallback/missing (see User Guide §5) |
| Forecast/analytics show placeholder or `unavailable` | Backend unreachable (`VITE_API_BASE_URL`) or UI fetcher path drift (`/api/forecast/{symbol}`, `/api/analytics/{symbol}`, `/api/backtest/run`) — call the backend routes directly |
| `npm` blocked by NVM (`NVM4306`) | Run `nvm reshim` (or `nvm doctor --autofix`), then `npm install` / `npm run typecheck` in `frontend/` |
| `market_state` shows delayed/stale instead of closed | Holiday-calendar stubs (see `docs/SSE_NOTES.md`, `docs/EURONEXT_NOTES.md`); freshness fallback is by design |
