# OneMarket Analyzer — V2 Part 1 (Auth/Billing/Ads live)

> **V1 is locked at `49a1604`. V2 Part 1 is merged (`7fe7948` PR #2
> `v2-auth-billing-ads` + `0f80d7c` native indices).**
> See **“V1 final status”** below for the V1 base, and **“V2 Part 1 status”**
> for what this tree adds (real JWT auth, hard tier gates, Stripe billing,
> compliant AdSense, cron resilience). Full plan: `docs/V2_PLAN.md`.

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
                            #   db/, observability/, workers/, auth/, tests/ (56 files —
                            #   rerun `pytest --collect-only` to confirm)
                            #   api/ (16 router prefixes, ~68 operations): ai, alerts,
                            #   analytics_api, audit, auth (POST register|login|refresh,
                            #   GET me), backtest, billing (POST checkout|portal|webhook,
                            #   GET status), cron, forecast, fx, instruments,
                            #   market_data (+ securities_router), markets, market_index
                            #   (GET /api/markets/{mic}/index — native caret indices),
                            #   liquidation_proxy (GET .../liquidation-proxy),
                            #   providers, screener, news, signals, health
                            #   auth/guards.py (get_current_user, require_tier 402,
                            #   require_admin 403) + security/passwords.py (bcrypt)
                            #   market_data/providers/: yfinance, stooq, akshare (SSE),
                            #   alpaca, finnhub_free, twelvedata_free (free tiers) + base
                            #   market_data/snapshot_store.py (gzip+zlib+zstd snapshots)
                            #   forecasting/accuracy.py + walk_forward + calibration/metrics
                            #   db/models.py + User (0008_users_auth) + writers.py
                            #   (ai_token_ledger, provider_health_history,
                            #   indicator_cache INSERT paths)
    Dockerfile
    requirements.txt        # root pins (numpy/pandas/sklearn/pytest);
                            # backend/requirements.txt is the image build
                            # (fastapi/uvicorn/sqlalchemy/yfinance/...)
  frontend/                 # React 18 + Vite + Tailwind (plain JSX, no TS)
    src/pages/              # 15 pages: Home, Welcome, Login (real JWT), Pricing,
                            #   CheckoutSuccess, Account, Search, Screener,
                            #   SecurityBrief, ForecastDetails, ProviderSettings,
                            #   BacktestLab, Watchlist, NotFound
    src/components/         # 27 components: ProvenanceBadge, MarketStateBadge,
                            #   CurrencyValue, FXProvenanceBanner, CalibrationChart,
                            #   AspiChart (native indices + Top-20 + disabled CSE card),
                            #   LiquidationPanel (per-market PROXY), ResearchSection,
                            #   AIOpinionCard, NewsPanel, MarketGraphs, AdSlot
                            #   (visible-only, tier-budgeted), RequireTier (UX mirror),
                            #   UpgradeModal, Layout (leaderboard + footer slots), ...
    src/features/           # security/ (SecurityBrief + PriceChart ≤1000 bars +
                            #   10 indicators), search/ (SearchBox), forecast/
                            #   (ForecastDetails), providers/ (ProviderSettings),
                            #   portfolio/ (PortfolioPlaceholder — v1 non-goal)
    src/api/                # client.js (zod-validated + Bearer injection, 401→/login,
                            #   402→upsell), markets.js, aspi.js (native caret registry
                            #   + Top-20 + cap-weighted), liquidation.js (PROXY),
                            #   auth.js (register/login/me) + authStub.js (guest path),
                            #   billing.js (checkout/portal), calibrationHistory.js,
                            #   backtestHistory.js, news.js, signals.js
                            #   10 vitest files (auth, AdSlot, RequireTier, ...)
    src/hooks/              # useWatchlist.js (^ allowed), useMarketLiquidity.js
                            #   (+ useMarketLiquidationProxy), useAuth.js
                            #   (replaces useCurrentUserStub call sites gradually)
    src/config/ads.js       # AD_INTENSITY {free:3, silver:2, gold:1, platinum:0}
                            #   by not-mounting, never hiding
  api/index.py              # Vercel serverless entry (from backend.api.main import app)
  api/requirements.txt
  infra/docker/.env.example # copy to infra/docker/.env, fill secrets (never commit .env)
  infra/migrations/         # 0001_initial … 0005_quote_snapshots + 0006_revamp.sql
                            # + 0007_horizons.sql + 0008_users_auth.sql (V2 users,
                            # additive-only) (+ 0006_snapshots.sql draft, superseded)
                            # + alembic.ini stub
  infra/scripts/            # verify_audit.py (alias), backup.sh, restore.sh
  scripts/                  # verify_v1.py (DoD checker), deploy_check.py, backfill_bars.py,
                            # bootstrap_admin.py (V2 admin platinum comped, idempotent)
  supabase/                 # migrations 0001_onemarket … 0008_users_auth (8 files,
                            # mirrors infra/ + RLS; note 0001 naming differs) + seed.sql
  docs/                     # 15 docs: API_CONTRACT (+auth/billing appendix), DATA_QUALITY,
                            #   DB_SCHEMA (users table), DATA_SOURCES_FREE, AI_PROVIDERS,
                            #   USER_GUIDE, OPERATIONS (+ADMIN/STRIPE rotation),
                            #   V1_CHECKLIST, SSE_NOTES, EURONEXT_NOTES, SECURITY
                            #   (+password/JWT/webhook), TESTING, DEPLOY_VERCEL_SUPABASE,
                            #   FAIL_CLOSED_CONTRACT (normative) + V2_PLAN (phases 0-6)
  vercel.json               # hosted deploy (Vite dist + api/index.py; frontend CSP for
                            #   ads, api default-src 'none' untouched) / render.yaml (split)
  .github/workflows/        # snapshots.yml + sp500-ingest.yml (non-blocking tick budgets
                            #   + retries) + local-selfhosted.yml.example
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

Apply the schema (rerun is safe — 7 migrations, Postgres):

```powershell
psql "postgresql://onemarket:<pw>@localhost:5432/onemarket" -f infra\migrations\0001_initial.sql
psql "postgresql://onemarket:<pw>@localhost:5432/onemarket" -f infra\migrations\0002_calibration.sql
psql "postgresql://onemarket:<pw>@localhost:5432/onemarket" -f infra\migrations\0003_alerts.sql
psql "postgresql://onemarket:<pw>@localhost:5432/onemarket" -f infra\migrations\0004_provider_secrets.sql
psql "postgresql://onemarket:<pw>@localhost:5432/onemarket" -f infra\migrations\0005_quote_snapshots.sql
psql "postgresql://onemarket:<pw>@localhost:5432/onemarket" -f infra\migrations\0006_revamp.sql
psql "postgresql://onemarket:<pw>@localhost:5432/onemarket" -f infra\migrations\0007_horizons.sql
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
npm test      # vitest run — 7 test files (client, client.schemas, bars, aspi,
              #   markets/liquidity, liquidation, authStub; rerun to confirm count)
npm run build # vite production build (must stay green)
```

The app must run fully with AI keys empty (AI disabled path, §7 item 7).

## What is implemented

- **Pages:** `/` Home (market status, liquidity, native indices + Top-20,
  liquidation proxy, watchlist, provider health, 1 AdSlot in-feed) · `/welcome` ·
  `/login` real JWT login/register (replaces stub) · `/pricing` (Stripe Checkout) ·
  `/checkout/success` · `/account` (tier/status/portal) · `/search` · `/screener`
  (silver-gated UI mirror) · `/security/:symbol` Security Brief (chart ≤1000 bars,
  2Y/5Y presets, 1 in-article AdSlot) · `/forecast/:symbol` (1 AdSlot) ·
  `/providers` (configure = platinum/admin) · `/backtest` Backtest Lab (gold-gated) ·
  `/watchlist` (FX-gated) · `*` NotFound. Portfolio remains an explicit
  placeholder (non-goal).
- **API (~68 operations across 16 router prefixes + `/health` + `/`):** `GET /health` ·
  `POST /api/auth/register|login|refresh` + `GET /api/auth/me` ·
  `POST /api/billing/checkout|portal|webhook` + `GET /api/billing/status` ·
  `/api/instruments/search|resolve|{id}` ·
  `/api/market_data/quote|bars|chart|indicators` (Free, top-of-funnel) + `/api/securities/{id}/quote|bars` ·
  `GET /api/forecast/{symbol}?horizon=1|7|14|21` + `/{symbol}/calibration/history` ·
  `GET /api/analytics/{symbol}` · `POST /api/backtest/run` + `GET /api/backtest/{symbol}` (gold) ·
  `POST /api/ai/insight` (free/silver by profile) + `POST /api/ai/forecast_opinion` +
  `POST /api/ai/deep_research_job` + `GET /api/ai/jobs/{id}` (silver, poll-job, no SSE) +
  `GET /api/ai/providers/performance` · `GET /api/audit/forecasts|ai_decisions` ·
  `/api/screener` (silver) · `/api/news` + `/api/news/symbol/{symbol}` · `/api/signals/top` ·
  `/api/alerts` CRUD + evaluate · `/api/providers/health|keys|budget` (keys/budget = platinum/admin) ·
  `/api/cron/ingest|calibrate|evaluate|snapshot|score|retention|health` (GET+POST each,
  non-blocking tick budgets + retries) · `/api/fx/pairs|rate|convert|rank` ·
  `/api/markets|overview|{mic}/liquidity|{mic}/liquidity/history|{mic}/index|{mic}/liquidation-proxy`.
  Full contract: `docs/API_CONTRACT.md` (+auth/billing appendix; M9 native index live).
  Machine truth: `python scripts/verify_v1.py` checks prefixes (V1 gate still green);
  count decorators with a search for `@router.get|post|...` in `backend/api/`.
- **Price charts:** backend serves `limit ≤ 1000`; presets
  `1D:5 / 1W:7 / 1M:30 / 3M:90 / 1Y:250 / 2Y:500 / 5Y:1000`; chart decimates to
  500 display candles (bucket-merge, extremes preserved) + 10 indicator
  overlays (SMA/EMA/RSI/MACD/BB/VWAP/ATR).
- **Liquidation (PROXY, per market):** deterministic volume-anomaly × ATR-range
  heuristic — NOT exchange data. Every view carries PROXY badge + methodology +
  `missing_fields: [liquidation-feed]`. Never fakes rows.
- **Indices + Top-20 (per market):** `GET /api/markets/{mic}/index` serves
  canonical benchmarks natively via yfinance — `^NYA` (XNYS), `^IXIC` (XNAS),
  `000001.SS` (XSHG), `^FCHI` (XPAR), `^AEX` (XAMS), `^BFX` (XBRU)
  (`is_proxy=false`); ETF proxies (`SPY`/`QQQ`/`EWQ`/`EWN`/`EWK`/`CAC.PA`/`IAEX.AS`)
  only on native miss (`is_proxy=true`). `validate_symbol` allows a single leading
  `^`; Alpaca/Finnhub/TwelveData skip `^` (yfinance-only). Top-20 turnover-sorted
  with screener-rank fallback, equal-weighted composite + cap-weighted mode
  (opts in only when every constituent carries finite `market_cap`, else honest
  equal fallback). CSE/XCOL is a disabled, probe-ready card — never data until
  `config/markets.yaml` enables it + vendor symbol is confirmed.
- **V2 Part 1 — Auth/Billing/Ads (merged PR #2, live):** custom JWT
  (bcrypt `$2b$`, HS256 15min access + httpOnly refresh rotation, email
  `lower(trim())`, password ≥10, duplicate → `409`, wrong password → `401`
  same-message) + `users` table (`0008_users_auth.sql` twin, RLS deny-by-default,
  additive-only, no FK backfill) + `scripts/bootstrap_admin.py` (admin
  `platinum/comped/is_admin`, zero Stripe objects). Backend enforces via
  `Depends(require_tier(...))`: `401` unauthenticated, `402` tier-too-low
  (`upgrade_required:true, min_tier`), `403` admin-only; frontend `<RequireTier>`
  + `UpgradeModal` mirror for UX/upsell only (never sole gate). Tiers:
  `quick_insight:forecast_assist:free, report:deep_research:screener:silver,
  backtest:gold, providers_configure:all_providers:platinum`. Stripe:
  Checkout + Portal + webhook (HMAC, `checkout.session.completed` upgrades,
  `deleted/past_due` downgrades to free, sig-less → `400`, idempotent).
  Cache keys scoped `u:{id}:t:{tier}:` (`indicator_cache` unscoped by design).
  Ads: AdSense visible-only (`AdSlot.jsx` + `config/ads.js`
  `free:3/silver:2/gold:1/platinum:0` by not-mounting, `IntersectionObserver`
  200px, `ads.txt`, Funding Choices/CMP, frontend CSP; `VITE_ADS_ENABLED=false`
  collapses to zero requests). Guest path kept (`authStub.js`); AI budgets still
  logged, never enforced.
- **Error-code truth (code owns):** bad horizon/profile → `422` with exact
  `detail` strings; FX rank without fresh provenance → `423 FX_PROVENANCE_MISSING`
  (not 409); unknown symbol → `404`; unauthenticated → `401`, tier-too-low → `402`
  (`upgrade_required:true`), admin-only → `403`; Stripe webhook sig-fail → `400`.
  Draft strings `INVALID_HORIZON / FORECAST_BLOCKED / AI_DISABLED / AI_VALIDATION_FAILED`
  are docs-only and never asserted by tests.

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
# 1. schema (forecasts + calibration + alerts + secrets + snapshots + revamp + horizons; rerun is safe)
psql "postgresql://onemarket:<pw>@localhost:5432/onemarket" -f infra\migrations\0001_initial.sql
psql "postgresql://onemarket:<pw>@localhost:5432/onemarket" -f infra\migrations\0002_calibration.sql
psql "postgresql://onemarket:<pw>@localhost:5432/onemarket" -f infra\migrations\0003_alerts.sql
psql "postgresql://onemarket:<pw>@localhost:5432/onemarket" -f infra\migrations\0004_provider_secrets.sql
psql "postgresql://onemarket:<pw>@localhost:5432/onemarket" -f infra\migrations\0005_quote_snapshots.sql
psql "postgresql://onemarket:<pw>@localhost:5432/onemarket" -f infra\migrations\0006_revamp.sql
psql "postgresql://onemarket:<pw>@localhost:5432/onemarket" -f infra\migrations\0007_horizons.sql

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

- [x] Deterministic forecast + calibrated confidence served (`GET /api/forecast/{symbol}?horizon=1|7|14|21`, horizons 1/7/14/21 only; `422` on bad horizon).
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
  `api/index.py` (`maxDuration 60`, crons `/api/cron/ingest 30 5 * * *`,
  `/api/cron/calibrate 30 6 * * *`), same-origin API (`VITE_API_BASE_URL` empty).
- **Render (split):** `render.yaml` — `onemarket-backend` (`uvicorn api.main:app`,
  `/health`) + `onemarket-frontend` static `dist` with `VITE_API_BASE_URL=<backend-url>`.
- **Supabase:** pooled `:6543` → `DATABASE_URL` (`NullPool`, `prepare_threshold=None`);
  direct `:5432` for DDL (0008 RLS: apply on direct only, never pooler);
  apply `supabase/migrations/0001_onemarket` … `0008_users_auth.sql`,
  seed `seed.sql` (`AAPL`, `600519.SS`, `MC.PA`). RLS enabled, anon reads 0.
- Details: `docs/DEPLOY_VERCEL_SUPABASE.md` · preflight: `python scripts/deploy_check.py`.

## Docs (v1 + V2 Part 1)

- **User Guide** (search→brief→forecast→AI→backtest→watchlist, `AAPL`/`600519.SS`/`MC.PA`,
  FX-gate, AI-disabled mode): `docs/USER_GUIDE.md`
- **Operations** (compose, env, backup/restore, retention, health/audit-verify, secret
  rotation): `docs/OPERATIONS.md`
- **Security** (key policy, hardening, redaction): `docs/SECURITY.md`
- **Testing** (pytest slices, Playwright, load smoke, failure matrix): `docs/TESTING.md`
- **Deploy** (Vercel + Supabase): `docs/DEPLOY_VERCEL_SUPABASE.md`
- **v1 Definition-of-Done checklist** (9 items with evidence + honest PARTIALs, non-goals,
  known limits): `docs/V1_CHECKLIST.md`
- Contract/quality/AI/venues/fail-closed: `docs/API_CONTRACT.md` (+auth/billing) ·
  `docs/DATA_QUALITY.md` · `docs/DB_SCHEMA.md` (users table) · `docs/DATA_SOURCES_FREE.md` ·
  `docs/AI_PROVIDERS.md` · `docs/SSE_NOTES.md` · `docs/EURONEXT_NOTES.md` ·
  `docs/FAIL_CLOSED_CONTRACT.md` (normative) · `docs/V2_PLAN.md` (V2 phases 0-6) ·
  `docs/SECURITY.md` (+password/JWT/webhook) · `docs/OPERATIONS.md` (+ADMIN/STRIPE)
- Machine-checks (rerun to confirm current tree):
  `python scripts/verify_v1.py` (stdlib-only; exit 1 on any FAIL — currently
  16 PASS, 2 PARTIAL, 0 FAIL) · `python scripts/deploy_check.py` (currently
  9 PASS, 0 FAIL) · `python -m py_compile scripts/verify_v1.py`

## Fail-closed contract (NO FALLBACKS — V1 lock-in, V2 extends)

> Delays are honest (`delay_minutes`, grade). Stale data and fallbacks are refused, never served.

- Live data or an honest error: `502` no live data · `423` AI disabled / FX refused · `422` bad input · `404` unknown · `401` unauthenticated · `402` tier-too-low · `403` admin-only. Never `200` with synthetic/stub/stale/cached-as-fresh. `fallback_used` is always `false` on success.
- Quotes/bars: first live wins (US: Alpaca → yfinance → Finnhub → TwelveData → Stooq; SSE: yfinance → AKShare; Euronext: yfinance → Stooq), else `502`. Bars DB-first with coverage gate plus a calendar-aware freshness gate (latest bar must cover the last completed session; 15min delay fine, days-old → refresh-or-`502`).
- AI: no key → `423`; live failure / stub → `502` (wire guard). `ai_enabled:false` returns the deterministic blend with no fake opinion.
- FX: Frankfurter → yfinance FX → `502`. No stub table served.
- Frontend: missing provenance throws to `ErrorState`; `422`/`502` throw immediately (only `404`/`501` fall through); stale/fallback renders `ErrorState` with retry, never a table + banner. Provider health shows measured latency only (uncalled → `unknown`, no fake `0ms`).
- Normative: `docs/FAIL_CLOSED_CONTRACT.md`.

## v1 status summary (M8 docs verification — base for V2)

- Backend suite: `python -m pytest backend/tests --collect-only -q -p no:cacheprovider`
  → **712 tests / 52 files at V1 audit; V2 Part 1 adds `test_auth.py`,
  `test_tier_gates.py`, `test_billing.py`, `test_cron_timeouts.py` (now 56 files — rerun
  `python -m pytest backend/tests -q -p no:cacheprovider` to confirm green).**
  `docs/V1_CHECKLIST.md` still cites the older 243-passed sign-off — treat the
  live `pytest` / `verify_v1.py` output as truth.
- DoD per `python scripts/verify_v1.py`: **16 PASS · 2 PARTIAL · 0 FAIL**
  (PARTIALs: DoD 4 contract-code strings docs-only; DoD 8 holiday-calendar stubs).
  PARTIALs in one line: docs-only contract codes (`400/409/AI_*` drafts vs code `422/423`);
  holiday-calendar stubs (licensed calendars required for production);
  sqlite stub without `audit_logs` (use `infra/scripts/verify_audit.py` against Postgres).
  Resolved: UI fetcher path drift, rank-refusal `423-vs-409` doc drift,
  missing `infra/scripts/verify_audit.py` alias (now exists).
- Non-goals hold (spec §1): no trading, no portfolio construction (placeholder only), no
  agents, no DL, no full OpenBB, no large backtest suite.
- Known limits: holiday stubs (licensed calendars required for production),
  no live-data offline (fail-closed `502`/`423`, never stub `200`),
  frontend `typecheck` is a no-op echo (plain JS) — run `npm run build` / `npm test` to verify.
- Details + evidence commands: `docs/V1_CHECKLIST.md`.

---

## V1 final status (last version before V2 — revamp lock-in)

Backend revamp (all IMPLEMENTED unless noted):

- Free real-time sources: 6 providers (yfinance default, stooq, akshare for SSE,
  alpaca/finnhub/twelvedata free tiers) with eligibility chain + provenance
  (`docs/DATA_SOURCES_FREE.md`). True realtime is US-only; EU/SSE delayed-15
  by design. One residual PARTIAL: on-demand `_fetch_and_store_bars` backfill
  is yfinance-only (cron ingest already uses the multi-source fallback chain).
- Deterministic forecasting: fast (forecast + feature caches, single bars+features
  pass) + accurate (confidence, walk-forward, calibration metrics). DCF/peer
  valuation + gradient-boost remain intentional stubs.
- Snapshots + scoring + DB: 15min/60min snapshot cron, accuracy scoring cron,
  `market_snapshots` + `forecast_accuracy` tables, `0006_revamp.sql` + `0007_horizons.sql`
  additive migrations; compression `gzip+json` + `delta-q100+gzip` + `zstd` (optional dep,
  gzip fallback). `lz4` not vendored (`zlib` covers that role); `zstd` in the
  DB CHECK. Retention: raw 30d / compressed 365d / accuracy 3y per `docs/DB_SCHEMA.md`
  (normative; `docs/DATA_QUALITY.md` still carries the older Redis-assumed spec).
- AI efficiency: 60s timeouts all profiles (debug window), transient-only retries, Semaphore(8),
  prompt budgets + truncation, circuit breaker, hybrid cache (in-memory +
  Redis-behind-`backend/cache.py`, best-effort). Token ledger dual-layer
  (in-memory + `ai_token_ledger` writers). PARTIAL by design: budgets logged
  never gated, no SSE streaming (`deep_research` is poll-job only).
- Healthchecks: 11-provider tracker + read-only endpoints + dashboard + 5% alerter.
  PARTIAL: Redis backing documented-not-wired (in-memory + cron mirror into
  `provider_health_history` only); `record()` has no live DB write-through.
- DB revamp: ORM covers 0001–0007, supabase pooled wiring (`:6543`, NullPool +
  `prepare_threshold: None`, pool_pre_ping), writers for
  ledger/health-history/indicator-cache. PARTIAL: indicator-cache
  writers have no callers yet; no `alembic/versions/` (rollout is raw
  `psql -f infra/migrations/000*.sql` per `docs/DB_SCHEMA.md`).

Frontend revamp: F1 bars (1000 + 2Y/5Y) DONE · F2 liquidation-PROXY (backend was
done, frontend added this lock-in) DONE · F3 research A/B DONE · F4 ASPI native
endpoint + 6 markets DONE, CSE/XCOL disabled probe-ready card (honest 422, never
data) · F5 Top-20 turnover + cap-weighted toggle DONE (cap mode falls back to
equal until `market_cap` is exposed) · Welcome/Login/tier stubs DONE at V1 lock-in
(superseded below by V2 real auth).

Verification at lock-in (rerun to confirm — commands, not frozen counts):

- Frontend: `npm run test -- --run` → 7 test files green at V1 audit (V2 adds
  `auth.test.js`, `AdSlot.test.js`, `RequireTier.test.js` — now 10 files);
  `npm run build` → clean (`vite build`). Frontend is plain `.js`/`.jsx` (no TS);
  `npm run typecheck` is an intentional no-op echo.
- Backend: `python -m pytest backend/tests --collect-only -q -p no:cacheprovider`
  → 712 tests / 52 files at V1 audit (V2 Part 1 → 56 files); full run
  `python -m pytest backend/tests -q -p no:cacheprovider` is the green gate
  (fail-closed: 502/423/422/401/402/403, never stub 200).
- Machine gates: `python scripts/verify_v1.py` → 16 PASS, 2 PARTIAL, 0 FAIL;
  `python scripts/deploy_check.py` → 9 PASS, 0 FAIL.
- Routes/migrations: ~68 `@router` operations in `backend/api/` across 16 prefixes
  (+auth, +billing); 8 migrations apply (`0008_users_auth.sql` last;
  `0006_snapshots.sql` is a superseded draft).

## V2 Part 1 status (first V2 slice — merged PR #2 + native indices)

- **Auth:** `POST /api/auth/register` (201, `409` duplicate) · `POST /api/auth/login`
  (same-message `401`) · `GET /api/auth/me` (live row, never JWT-cached) ·
  `POST /api/auth/refresh` (httpOnly rotation). `scripts/bootstrap_admin.py` →
  `{tier:platinum, is_admin:true, subscription_status:comped}`, zero Stripe objects.
- **Tier gates (backend enforces):** `require_tier` → `401/402{upgrade_required,min_tier}`,
  `require_admin` → `401/403`. Matrix: free→deep_research/screener `402`,
  free/silver→backtest `402`, gold→backtest `200`, spoofed `X-Tier` ignored, admin bypass.
  Quote/bars/chart stay Free. Tests: `test_auth.py`, `test_tier_gates.py`.
- **Billing:** `POST /api/billing/checkout {silver|gold|platinum}` → `{url}`;
  `POST /api/billing/webhook` (HMAC, `400` on sig-fail; completed upgrades, deleted
  downgrades, idempotent); `POST /api/billing/portal` + `GET /api/billing/status`
  (no secrets). Tests: `test_billing.py` (mocked Stripe SDK).
- **Ads (compliant, visible-only):** `<AdSlot/>` + `AD_INTENSITY`
  `{free:3, silver:2, gold:1, platinum:0}` by not-mounting (zero requests for suppressed
  tiers), `IntersectionObserver` 200px, CLS reserve, `ads.txt`, Funding Choices/CMP,
  frontend CSP in `vercel.json`. Tests: `AdSlot.test.js`.
- **Cron resilience:** non-blocking tick budgets + workflow retries
  (`snapshots.yml`, `sp500-ingest.yml`, `test_cron_timeouts.py`); self-hosted example
  workflow + `docs/OPERATIONS.md` env (`ADMIN_*`, `STRIPE_*`, `VITE_ADS_*`).
- **Native indices (follow-up `0f80d7c`):** `^NYA/^IXIC/000001.SS/^FCHI/^AEX/^BFX`
  live via yfinance (`is_proxy=false`), ETF fallback only, `^` allowed, Alpaca skipped.
- **Verify V2 Part 1:**
  ```powershell
  psql "..." -f infra\migrations\0008_users_auth.sql
  python -m pytest backend/tests/test_auth.py backend/tests/test_tier_gates.py backend/tests/test_billing.py backend/tests/test_cron_timeouts.py -q -p no:cacheprovider
  python scripts/verify_v1.py  # must stay 16 PASS, 2 PARTIAL, 0 FAIL
  cd frontend; npm test -- --run; npm run build
  ```
- **Remaining V2 candidates (not promises):** confirm CSE vendor symbol + enable XCOL;
  expose `market_cap` for true cap-weighted Top-20; live health write-through;
  streaming for deep-research; FK backfill (`NOT VALID` + `VALIDATE`) for `user_id` stubs.
