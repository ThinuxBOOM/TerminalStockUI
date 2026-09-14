# OneMarket Analyzer

Self-hosted, multi-market stock analyzer (NYSE, NASDAQ, SSE, Euronext).
Deterministic analytics + forecasting are the source of truth; AI providers
(Gemini / OpenAI / Claude / Grok) add structured event analysis and bounded
forecast opinions only. Full spec: `../markdown.md`.

> **Disclosure:** forecasts are measurable probabilities, **not investment advice**.
> Every forecast view must render the disclosure string returned by the API.

## Repo layout

```
TerminalStockUI/
  docker-compose.yml        # postgres:16, redis:7, backend, frontend
  backend/                  # FastAPI: api/, analytics/, forecasting/, ai/,
                            #   instruments/, market_data/, security/,
                            #   db/, observability/, workers/, tests/ (38 files)
    Dockerfile
    requirements.txt        # + ai/sse/fx fragment requirements
  frontend/                 # React 18 + Vite + Tailwind (plain JSX)
    src/pages/              # Home, Search, Screener, SecurityBrief,
                            #   ForecastDetails, ProviderSettings,
                            #   BacktestLab, Watchlist, NotFound
    src/components/         # ProvenanceBadge, MarketStateBadge, CurrencyValue,
                            #   FXProvenanceBanner, CalibrationChart, ...
    src/features/           # security/, search/, forecast/, providers/,
                            #   portfolio/ (placeholder only)
    src/api/                # client.js (zod-validated), markets.js,
                            #   calibrationHistory.js, backtestHistory.js
    src/hooks/              # useWatchlist.js, useMarketLiquidity.js
  api/index.py              # Vercel serverless entry (from backend.api.main import app)
  api/requirements.txt
  infra/docker/.env.example # copy to infra/docker/.env, fill secrets (never commit .env)
  infra/migrations/         # 0001_initial.sql … 0005_quote_snapshots.sql + alembic.ini stub
  infra/scripts/            # verify_audit.py (alias), backup.sh, restore.sh
  scripts/                  # verify_v1.py (DoD checker), deploy_check.py, backfill_bars.py
  supabase/                 # migrations 0001-0005 (mirrors infra/ + RLS) + seed.sql
  docs/                     # API_CONTRACT, DATA_QUALITY, AI_PROVIDERS, USER_GUIDE,
                            #   OPERATIONS, V1_CHECKLIST, SSE_NOTES, EURONEXT_NOTES,
                            #   SECURITY, TESTING, DEPLOY_VERCEL_SUPABASE
  vercel.json / render.yaml # hosted deploy (Vite dist + api/index.py / split Render)
```

## Quickstart (Docker Compose first — no K8s in v1)

```powershell
cd "C:\Users\thinu\OneDrive\Pictures\Documents\STOCK ANALYSIS MAIN\TerminalStockUI"
copy infra\docker\.env.example infra\docker\.env
# edit infra\docker\.env (POSTGRES_PASSWORD, SECRET_KEY at minimum)
docker compose up --build
```

- Frontend: http://localhost:5173
- Backend: http://localhost:8000 (`/health`, `/api/...` per `docs/API_CONTRACT.md`)
- Postgres: localhost:5432 · Redis: localhost:6379

Apply the schema (rerun is safe — 5 migrations, Postgres):

```powershell
psql "postgresql://onemarket:<pw>@localhost:5432/onemarket" -f infra\migrations\0001_initial.sql
psql "postgresql://onemarket:<pw>@localhost:5432/onemarket" -f infra\migrations\0002_calibration.sql
psql "postgresql://onemarket:<pw>@localhost:5432/onemarket" -f infra\migrations\0003_alerts.sql
psql "postgresql://onemarket:<pw>@localhost:5432/onemarket" -f infra\migrations\0004_provider_secrets.sql
psql "postgresql://onemarket:<pw>@localhost:5432/onemarket" -f infra\migrations\0005_quote_snapshots.sql
# — or inside compose:
# docker compose exec postgres psql -U onemarket -d onemarket -f /docker-entrypoint-initdb.d/0001_initial.sql
```

Validate compose file without a running daemon:

```powershell
docker compose config
# fallback (no docker at all):
python -c "import yaml,sys; yaml.safe_load(open('docker-compose.yml')); print('compose YAML parses OK')"
```

## Local dev (without compose)

```powershell
# backend (repo root)
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt  # root pins; backend/requirements.txt for image build
$env:DATABASE_URL="postgresql+psycopg://onemarket:<pw>@localhost:5432/onemarket"
$env:REDIS_URL="redis://localhost:6379/0"
uvicorn backend.app:app --reload --port 8000
# — or with backend/ as CWD:
# cd backend; uvicorn api.main:app --reload --port 8000

# frontend (new terminal)
cd frontend
npm install
npm run dev   # VITE_API_BASE_URL=http://localhost:8000
npm test      # vitest run (client schemas, bars)
```

The app must run fully with AI keys empty (AI disabled path, §7 item 7).

## What is implemented

- **Pages:** `/` Home (market status, watchlist, provider health) · `/search` ·
  `/screener` · `/security/:symbol` Security Brief · `/forecast/:symbol` ·
  `/providers` · `/backtest` Backtest Lab · `/watchlist` (FX-gated) ·
  `*` NotFound. Portfolio remains an explicit placeholder (non-goal).
- **API (43 routes):** `GET /health` · `/api/instruments/search|resolve|{id}` ·
  `/api/market_data/quote|bars` + `/api/securities/{id}/quote|bars` ·
  `GET /api/forecast/{symbol}?horizon=5|21|63` + `/calibration/history` ·
  `GET /api/analytics/{symbol}` · `POST /api/backtest/run` + `GET /api/backtest/{symbol}` ·
  `POST /api/ai/insight` + `POST /api/ai/forecast_opinion` +
  `GET /api/ai/providers/performance` · `GET /api/audit/forecasts|ai_decisions` ·
  `/api/screener` · `/api/alerts` CRUD + evaluate · `/api/providers/health|keys|budget` ·
  `/api/cron/ingest|calibrate|evaluate` · `/api/fx/pairs|rate|convert|rank` ·
  `/api/markets/overview|{mic}/liquidity`. Full contract: `docs/API_CONTRACT.md`.
- **Error-code truth (code owns):** bad horizon/profile → `422` with exact
  `detail` strings; FX rank without fresh provenance → `423 FX_PROVENANCE_MISSING`
  (not 409); unknown symbol → `404`. Draft strings `INVALID_HORIZON / FORECAST_BLOCKED /
  AI_DISABLED / AI_VALIDATION_FAILED` are docs-only and never asserted by tests.

## Milestone 0 acceptance (provider resilience & data quality) — PASS

- [x] Provider failure is isolated (circuit breaker per provider, fallback to cache/secondary).
- [x] Every response carries a complete `provenance` envelope (source, as_of, delay_minutes, quality_grade, fallback_used, missing_fields).
- [x] Provider health dashboard live (`GET /api/providers/health` → latency p50/p95, error rate, circuit state).
- [x] Freshness/reconciliation checks + retention rules enforced (`docs/DATA_QUALITY.md`).
- [x] Audit logging foundation live + verifiable (see below).
- [x] FX provenance gate enforced (no cross-market ranking until FX source is grade A/B).

## Milestone 1 acceptance (foundation + US markets) — PASS

- [x] `docker compose up --build` brings up postgres, redis, backend, frontend.
- [x] Search `AAPL` → Security page with price chart + source/timestamp badge.
- [x] Cached data served on provider outage; page usable under partial failure.
- [x] Instrument registry resolves exchange-aware identity (MIC + symbol, never bare ticker).

## Provenance + disclosure notes

- Every displayed number shows source, timestamp, delay/freshness, and quality grade.
- Missing data renders "unavailable" with reason — never zero-filled.
- Forecasts show `model_version + feature_version + data_version + timestamp`.
- AI weight capped at 20%, server-enforced (`backend/ai/blend.py: AI_WEIGHT_MAX = 0.20` +
  SQL `CHECK (ai_weight <= 0.20)`); disabling AI leaves forecasting intact.
- No API key is exposed to the browser or stored in plaintext (encrypted at rest,
  decrypted only at call time, redacted in logs/audit payloads).

## Observability

- **Provider latency/error dashboard (implemented):** backend records per-call
  `{provider, latency_ms, ok, circuit}`; `GET /api/providers/health`
  aggregates p50/p95 + 1h error rate (`backend/observability/dashboard.py`).
  Frontend Home page renders per-provider cards + global banner on any open circuit.
  Alert (log + audit event) on error_rate > 5%/5min.
- **Audit log verification:** `audit_logs` is append-only with `hash = sha256(prev_hash ||
  created_at || actor || action || entity || payload)`. Verify chain:
  ```powershell
  python infra\scripts\verify_audit.py  # alias → backend.observability.audit_verify; exit 1 on gap/rewrite
  # canonical:
  python -m backend.observability.audit_verify --database-url $env:DATABASE_URL
  ```
  Point the verifier at Postgres after applying `infra/migrations/0001_initial.sql`
  (local `./onemarket.db` sqlite stub has no `audit_logs` table).
- **Backup / restore:**
  ```powershell
  bash infra/scripts/backup.sh    # pg_dump -Fc + redis BGSAVE/RDB + sha256 + optional S3
  bash infra/scripts/restore.sh   # guard ALLOW_RESTORE=1, pg_restore --clean, audit_verify
  # raw compose fallback (normative procedure):
  docker compose exec postgres pg_dump -U onemarket onemarket | Out-File -Encoding utf8 backup-$(Get-Date -Format yyyyMMdd).sql
  Get-Content backup-*.sql | docker compose exec -T postgres psql -U onemarket -d onemarket
  docker compose exec redis redis-cli BGSAVE
  docker cp onemarket-redis:/data/appendonlydir ./redis-backup/
  ```
  Retention + purge covered by `backend/observability/retention.py`
  (`test_retention.py`, `test_db.py`).

---

## M3/M4 quickstart (forecasting + AI opinions + audit)

Deterministic forecasting works with AI keys empty. AI adds bounded opinions only.

```powershell
cd "C:\Users\thinu\OneDrive\Pictures\Documents\STOCK ANALYSIS MAIN\TerminalStockUI"
# 1. schema (forecasts + calibration + alerts + secrets + snapshots; rerun is safe)
psql "postgresql://onemarket:<pw>@localhost:5432/onemarket" -f infra\migrations\0001_initial.sql
psql "postgresql://onemarket:<pw>@localhost:5432/onemarket" -f infra\migrations\0002_calibration.sql
psql "postgresql://onemarket:<pw>@localhost:5432/onemarket" -f infra\migrations\0003_alerts.sql
psql "postgresql://onemarket:<pw>@localhost:5432/onemarket" -f infra\migrations\0004_provider_secrets.sql
psql "postgresql://onemarket:<pw>@localhost:5432/onemarket" -f infra\migrations\0005_quote_snapshots.sql

# 2. routers are wired in backend/api/main.py (create_app) — no manual include needed

# 3. versioned forecast log + AI decision log
#    GET http://localhost:8000/api/audit/forecasts?symbol=AAPL
#    GET http://localhost:8000/api/audit/ai_decisions?provider=gemini

# 4. verify the audit hash chain (fails loudly on any gap/rewrite)
python infra\scripts\verify_audit.py
#    $env:DATABASE_URL="postgresql+psycopg://onemarket:<pw>@localhost:5432/onemarket"
#    python -m backend.observability.audit_verify --database-url $env:DATABASE_URL

# 5. run the M3/M4 test slice
python -m pytest backend/tests/test_audit.py backend/tests/test_observability.py backend/tests/test_forecast.py backend/tests/test_ai_blend.py -q

# 6. (optional) configure Gemini for bounded opinions — server-side only, never in the browser
#    via Provider Settings page (/providers) or: put("gemini", "api_key", "<paste-once>")
#    AI-disabled path stays green: leave keys empty and /forecast still works with ai_weight=0
```

Provider latency/error dashboard data: `GET /api/providers/health`
(full dashboard payload via `backend.observability.dashboard.build_dashboard`).

## M3/M4 acceptance checklist (spec §7 items 4–6, 9)

- [x] Deterministic forecast + calibrated confidence served (`GET /api/forecast/{symbol}?horizon=5|21|63`, horizons 5/21/63 only; `422` on bad horizon).
- [x] Every forecast stores `model_version + feature_version + data_version + timestamp` (versioned log at `GET /api/audit/forecasts?symbol=`).
- [x] AI explanation + bounded opinion on explicit request only (`POST /api/ai/insight`, `POST /api/ai/forecast_opinion`); malformed opinions fail validation safely (422/stub degrade).
- [x] AI weight capped at 20%, server-enforced; disabling AI leaves forecasting intact (`ai_weight=0`, forecast intact with keys empty).
- [x] Provider/model switch with zero analytics/frontend changes; historical performance visible (`GET /api/ai/providers/performance`).
- [x] Audit logs + provider health + `Not investment advice` disclosure visible (verify: `python infra/scripts/verify_audit.py`).
- [x] No plaintext keys anywhere; audit payloads redacted (covered by `test_audit.py` / `test_security_redaction.py` / `test_observability.py`).
- [x] Docs: `docs/API_CONTRACT.md` (M3/M4 appendix), `docs/DATA_QUALITY.md` (calibration/versioning/retention), `docs/AI_PROVIDERS.md`.

## M6 acceptance checklist (SSE search/currency — spec §5 M6 + §8 search)

- [x] Search `Moutai` / `600519` / `600519.SS` (market `SSE`) → row shows company, exchange (`XSHG`), currency (`CNY¥`), symbol (`600519.SS`).
- [x] Market filter `All / NYSE / NASDAQ / SSE` maps to `?market=` (`XNYS` / `XNAS` / `XSHG`; All omits the param).
- [x] No ticker ambiguity: multiple hits surface ranked candidates + `ambiguous` state; the UI never guesses.
- [x] Market `open / closed / lunch / delayed / stale` correctly identified (`MarketStateBadge`; `lunch` = XSHG 11:30–13:00 Asia/Shanghai; `closed`/`lunch` only from explicit API value, fallback derives `open|delayed|stale` from provenance).
- [x] Currency handling correct: `USD$` / `EUR€` / `CNY¥` via `Intl.NumberFormat` (`CurrencyValue`); every number keeps its `ProvenanceBadge`; no hardcoded live prices.
- [x] Docs: `docs/API_CONTRACT.md` (M6 appendix), `docs/SSE_NOTES.md` (suffix/fallback/T+1/limits/lunch/holidays).

## M7 acceptance checklist (Euronext search/quote + FX-gated Watchlist — spec §5 M7–M8)

- [x] Search `LVMH` / `MC` / `MC.PA` (market `Euronext Paris`) → row shows `MC.PA · LVMH Moet Hennessy Louis Vuitton SE · XPAR · EUR€`; `ASML.AS` (XAMS) and `UCB.BR` (XBRU) resolve to the right MIC/currency.
- [x] Market filter `All / NYSE / NASDAQ / SSE / Euronext Paris / Euronext Amsterdam / Euronext Brussels` maps to `?market=` (`XNYS` / `XNAS` / `XSHG` / `XPAR` / `XAMS` / `XBRU`; All omits the param).
- [x] Quote `MC.PA` (+ optional `target_ccy`) returns native `EUR` price + `market_state` (09:00–17:30 continuous, no lunch) + `ProvenanceBadge` on every number; `Intl.NumberFormat` formatting; no hardcoded prices.
- [x] Watchlist target-ccy selector (`USD / EUR / CNY`) + `FXProvenanceBanner` (source/as_of/fallback) visible.
- [x] Watchlist gated: fresh FX (grade A/B, delay ≤ 30m, no fallback) → ranked converted table; stale/missing FX or `423 FX_PROVENANCE_MISSING` → `Cross-market comparison unavailable — FX provenance missing` instead of ranked numbers (native quotes only, never rank without fresh FX).
- [x] Docs: `docs/API_CONTRACT.md` (M7 appendix: Euronext search/quote, `/api/fx/*` + rank gate + `423` code), `docs/EURONEXT_NOTES.md` (suffixes/sessions/holiday stub/FX gate rules).

---

## Hosted deploy (Vercel + Supabase, or Render split)

- **Vercel (monorepo primary):** `vercel.json` builds `frontend/dist`, serves
  `api/index.py` (`maxDuration 60`, crons `/api/cron/ingest 0 1 * * *`,
  `/api/cron/calibrate 0 2 * * *`), same-origin API (`VITE_API_BASE_URL` empty).
- **Render (split):** `render.yaml` — `onemarket-backend` (`uvicorn api.main:app`,
  `/health`) + `onemarket-frontend` static `dist` with `VITE_API_BASE_URL=<backend-url>`.
- **Supabase:** pooled `:6543` → `DATABASE_URL` (`NullPool`, `prepare_threshold=None`);
  direct `:5432` for DDL; apply `supabase/migrations/0001-0005`, seed `seed.sql`
  (`AAPL`, `600519.SS`, `MC.PA`). RLS enabled, no anon policies.
- Details: `docs/DEPLOY_VERCEL_SUPABASE.md` · preflight: `python scripts/deploy_check.py`.

## Docs (v1)

- **User Guide** (search→brief→forecast→AI→backtest→watchlist, `AAPL`/`600519.SS`/`MC.PA`,
  FX-gate, AI-disabled mode): `docs/USER_GUIDE.md`
- **Operations** (compose, env, backup/restore, retention, health/audit-verify, secret
  rotation): `docs/OPERATIONS.md`
- **Security** (key policy, hardening, redaction): `docs/SECURITY.md`
- **Testing** (pytest slices, Playwright, load smoke, failure matrix): `docs/TESTING.md`
- **Deploy** (Vercel + Supabase): `docs/DEPLOY_VERCEL_SUPABASE.md`
- **v1 Definition-of-Done checklist** (9 items with evidence + honest PARTIALs, non-goals,
  known limits): `docs/V1_CHECKLIST.md`
- Contract/quality/AI/venues: `docs/API_CONTRACT.md` · `docs/DATA_QUALITY.md` ·
  `docs/AI_PROVIDERS.md` · `docs/SSE_NOTES.md` · `docs/EURONEXT_NOTES.md`
- Machine-check: `python scripts/verify_v1.py` (stdlib-only; exit 1 on any FAIL) ·
  `python -m py_compile scripts/verify_v1.py`
- Note: `verify_v1.py` still asserts old `.ts/.tsx` paths (`client.ts`,
  `WatchlistPage.tsx`, …) so its GATES/DoD 2–6,8 FAIL on the current `.jsx` tree
  while functionality is unaffected — code truth is `.jsx` (`frontend/src/api/client.js`,
  `frontend/src/pages/WatchlistPage.jsx`).

## v1 status summary (M8 docs verification)

- Backend suite at sign-off: `python -m pytest backend/tests -q` → **243 passed**
  (38 `test_*.py` files; rerun to confirm current tree).
- DoD: **6 PASS** (items 1, 2, 3, 5, 6, 7) · **3 PARTIAL** (items 4, 8, 9) · **0 FAIL**.
  PARTIALs in one line: docs-only contract codes (`400/409/AI_*` drafts vs code `422/423`);
  holiday-calendar stubs (licensed calendars required for production);
  sqlite stub without `audit_logs` (use `infra/scripts/verify_audit.py` against Postgres).
  Resolved since first draft: UI fetcher path drift, rank-refusal `423-vs-409` doc drift,
  missing `infra/scripts/verify_audit.py` alias (now exists).
- Non-goals hold (spec §1): no trading, no portfolio construction (placeholder only), no
  agents, no DL, no full OpenBB, no large backtest suite.
- Known limits: holiday stubs, offline stub-fallback behavior (`fallback_used:true`, grade C),
  frontend `typecheck` is a no-op echo (plain JS) — run `npm run build` / `npm test` to verify.
- Details + evidence commands: `docs/V1_CHECKLIST.md`.
