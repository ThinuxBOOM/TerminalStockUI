# OneMarket Analyzer — Deploy: GitHub + Vercel + Supabase (v1)

Compose-first stays the local/dev path (`docs/OPERATIONS.md`). This doc is the
**hosted** path: GitHub repo → Supabase Postgres → Vercel (Vite frontend +
serverless API). No credentials are stored here — commands only.

> Scope note: this doc + `.github/` + `scripts/deploy_check.py` are owned by
> the CI/docs agent. Runtime code (`backend/`, `frontend/`, `api/`,
> `supabase/`, `infra/`, `vercel.json`) is owned by other agents — read here,
> never edited here. `scripts/deploy_check.py` validates those cross-owned
> files when present and reports SKIP (not FAIL) while any are still pending.

Preflight (repo root):

```powershell
python scripts/deploy_check.py
```

---

## 1. GitHub: init + create + push (commands only — do NOT run without credentials)

```powershell
cd "C:\Users\thinu\OneDrive\Pictures\Documents\STOCK ANALYSIS MAIN\onemarket-analyzer"

git init -b main
git add .
git status --short            # review: no .env, no *.db, no .vercel/ (see .gitignore)
git commit -m "chore: initial OneMarket Analyzer import (v1)"

gh repo create <OWNER>/onemarket-analyzer --private --source=. --remote=origin --push
# — or, for an already-created empty repo:
# git remote add origin https://github.com/<OWNER>/onemarket-analyzer.git
# git push -u origin main
```

CI (`.github/workflows/ci.yml`) runs on every push/PR:

- `backend` job: Python 3.11, pip cache on `backend/requirements.txt`,
  `python -m pytest backend/tests -q` (must stay green; stub data, no network).
- `frontend` job: Node 20, npm cache, `npm install --prefix frontend` then
  `npm run typecheck --prefix frontend` (currently `continue-on-error: true`
  until node_modules/lockfile stabilizes — see the workflow comment).
- `deploy-check` job: `python scripts/deploy_check.py`.

Branch protection (recommended on `main`): require the `backend` job to pass;
keep `frontend` advisory until `continue-on-error` is removed.

---

## 2. Supabase: project + schema + seed + pooled URL

1. Create a project at https://supabase.com/dashboard → note the
   **Project URL** and the **pooler (pooled) connection string**
   (Database → Connect → Transaction pooler, port `6543`):
   `postgresql://postgres.<REF>:<PASSWORD>@aws-0-<REGION>.pooler.supabase.com:6543/postgres?pgbouncer=true`
2. Apply the schema in the Supabase SQL editor (or `psql` with the
   **direct** connection, port `5432`). Canonical file:
   `supabase/migrations/0001_onemarket.sql`
   (equivalent fallback: `infra/migrations/0001_initial.sql`).
   It creates the 4 v1 tables: `instruments`, `price_bars`, `forecasts`,
   `audit_logs` (rerun-safe: `CREATE TABLE IF NOT EXISTS`):
   ```powershell
   psql "postgresql://postgres:<PASSWORD>@db.<REF>.supabase.co:5432/postgres" `
     -f supabase\migrations\0001_onemarket.sql
   ```
3. Seed (optional smoke row — exchange-aware identity, never a bare ticker):
   ```sql
   insert into instruments (exchange_mic, exchange_symbol, company_name, currency, country)
   values ('XNAS', 'AAPL', 'Apple Inc.', 'USD', 'US')
   on conflict do nothing;
   ```
4. Copy the **pooled** URL from step 1 → this becomes Vercel's `DATABASE_URL`
   (pooler mode is required on serverless; direct `5432` exhausts connections).

---

## 3. Vercel: import repo + build settings + env vars

1. Vercel dashboard → Add New → Project → Import the GitHub repo from §1.
2. Configure — already committed as `vercel.json` (owned by the Vercel
   agent; dashboard values below must match it):
   | Setting | Value (`vercel.json`) |
   |---|---|
   | Framework Preset | Vite (`"framework": "vite"`) |
   | Root Directory | `./` (repo root) |
   | Build Command | `npm run build --prefix frontend` (`tsc --noEmit && vite build`) |
   | Output Directory | `frontend/dist` (Vite default `dist` under `frontend/`) |
   | Install Command | `npm install --prefix frontend` (no frontend lockfile yet; use `npm ci` once one lands) |
   | Rewrites | `/api/(.*)` → `/api` (serverless entry `api/index.py`); SPA fallback `/((?!api/).*)` → `/index.html` |
3. Serverless entry `api/index.py` (owned by the backend/Vercel agent):
   imports `from backend.api.main import app` and exposes `handler`
   (Mangum wrapper, lifespan off; falls back to the raw ASGI app). Local dev
   is unchanged: `uvicorn backend.app:app --port 8000`.
4. Environment Variables (Project → Settings → Environment Variables —
   Production + Preview; never commit real values):
   | Variable | Value / source | Required |
   |---|---|---|
   | `DATABASE_URL` | Supabase **pooled** URL from §2 step 4 | yes |
   | `SECRET_KEY` | `openssl rand -hex 32` (rotating invalidates stored provider keys — re-store them, cf. `docs/OPERATIONS.md` §6) | yes |
   | `VITE_API_BASE_URL` | `https://<your-app>.vercel.app` (same deployment; local default `http://localhost:8000`, cf. `frontend/.env.example`) | yes |
   | `REDIS_URL` | Upstash Redis URL (optional — app runs in-memory without it; see §4) | no |
   | `GEMINI_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `XAI_API_KEY` | Leave **empty** for the AI-disabled path (forecasting stays intact, `ai_weight=0`); production keys are stored via the Provider Settings flow, encrypted server-side | no |
5. Deploy. Every push to `main` redeploys; PRs get Preview deployments.

Key-name reference: `infra/docker/.env.example` (compose names) and
`frontend/.env.example` (`VITE_API_BASE_URL`).

---

## 4. Serverless limits (read before relying on hosted behavior)

- **Timeouts:** Hobby `10 s`, Pro up to `60 s` per function invocation.
  Keep `/api/forecast/*` + `/api/backtest/run` on the deterministic
  fast path; long recomputes belong in the worker, not the request.
- **Cold starts:** first hit after idle is slower (Python import cost).
  Mitigate with small `api/index.py` imports and Vercel Fluid/Skew
  protection defaults; provider-health caching (Redis TTL 5 min–24 h)
  hides most of it.
- **No long-lived workers:** `backend/workers/jobs.py` (ingestion, report
  generation, alert evaluation) **cannot** run as a daemon on Vercel.
  Use **Vercel Cron** (`vercel.json` `crons`, **pending, Vercel agent**)
  hitting a lightweight `/api/cron/<job>` endpoint, or an **external
  worker** (local `docker compose run worker`, Fly.io/Render, GitHub
  Actions `schedule:`) against the Supabase DB.
- **Redis is optional:** without `REDIS_URL` the app degrades to in-memory
  cache/queue (single-instance semantics). For multi-instance correctness
  add **Upstash Redis** (serverless-friendly) and set `REDIS_URL`.
- **Ephemeral filesystem:** never rely on local `./onemarket.db` or written
  files on Vercel — Postgres (Supabase) is the store; audit chain stays
  verifiable via `python -m backend.observability.audit_verify`.

---

## 5. Verify checklist (post-deploy)

```powershell
$BASE = "https://<your-app>.vercel.app"

curl "$BASE/health"                                  # expect 200
curl "$BASE/api/forecast/AAPL?horizon_days=21"       # deterministic forecast + versions + disclosure
curl "$BASE/api/audit/forecasts?symbol=AAPL"         # versioned forecast log (model/feature/data versions)
curl "$BASE/api/providers/health"                    # latency p50/p95 + error rate + circuit state
```

- [ ] `/health` 200 on Production **and** Preview URL.
- [ ] `/api/forecast/AAPL?horizon_days=21` returns `direction_prob`,
      `model_version + feature_version + data_version + timestamp`, and the
      `Not investment advice` disclosure; invalid horizon (`?horizon_days=7`)
      is 422, never 500.
- [ ] Audit: `GET /api/audit/forecasts?symbol=AAPL` non-empty after a
      forecast; chain verifiable against Supabase
      (`python -m backend.observability.audit_verify --database-url $DATABASE_URL`).
- [ ] FX gate: `POST /api/fx/rank` with stale/missing FX refuses with
      `423` + `code FX_PROVENANCE_MISSING` (docs-only `409` drift noted in
      `docs/V1_CHECKLIST.md`); Watchlist shows
      `Cross-market comparison unavailable — FX provenance missing`.
- [ ] AI-disabled path: with keys empty, forecast works (`ai_weight=0`);
      AI endpoints refuse `409 AI_DISABLED`-semantics (code uses HTTP 422 /
      disabled blend — same fail-safe behavior).
- [ ] CI green on the deploy commit (backend job required; frontend
      advisory until `continue-on-error` is removed).

---

## 6. Rollback

- **Vercel (instant):** Deployments → pick the last-known-good deployment →
  **Promote to Production** (or `vercel rollback` with the CLI). No rebuild,
  no data migration — traffic flips in seconds. Preview URLs of older
  deploys stay addressable for diffing.
- **Supabase (PITR note):** schema `0001` is additive/rerun-safe, so code
  rollback needs no DB change. For data corruption use Supabase Dashboard →
  Database → Backups → **Point-in-Time Recovery** (available on paid plans;
  restores to a new project/branch — re-point `DATABASE_URL` after restore
  and re-verify §5). Free-tier: rely on `pg_dump` snapshots per
  `docs/OPERATIONS.md` §3 before risky migrations.
- **Secret rotation after rollback:** if a bad deploy ever logged key
  material, rotate that provider key immediately via the settings flow
  (audit rows are append-only and cannot be redacted retroactively).
