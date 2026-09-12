# OneMarket Analyzer

Self-hosted, multi-market stock analyzer (NYSE, NASDAQ, SSE, Euronext).
Deterministic analytics + forecasting are the source of truth; AI providers
(Gemini / OpenAI / Claude / Grok) add structured event analysis and bounded
forecast opinions only. Full spec: `../markdown.md`.

> **Disclosure:** forecasts are measurable probabilities, **not investment advice**.
> Every forecast view must render the disclosure string returned by the API.

## Repo layout (who owns what)

```
onemarket-analyzer/
  docker-compose.yml        # infra (this agent): postgres:16, redis:7, backend, frontend
  backend/Dockerfile        # infra placeholder — backend agent owns backend/ source
  frontend/Dockerfile       # infra placeholder — frontend agent owns frontend/ source
  infra/docker/.env.example # copy to infra/docker/.env, fill secrets (never commit .env)
  infra/migrations/         # alembic.ini stub + 0001_initial.sql
  docs/API_CONTRACT.md      # REST + provenance envelope contract
  docs/DATA_QUALITY.md      # grades, freshness, retention
  backend/                  # backend agent — do not touch from infra
  frontend/                 # frontend agent — do not touch from infra
```

## Quickstart (Docker Compose first — no K8s in v1)

```powershell
cd "C:\Users\thinu\OneDrive\Pictures\Documents\STOCK ANALYSIS MAIN\onemarket-analyzer"
copy infra\docker\.env.example infra\docker\.env
# edit infra\docker\.env (POSTGRES_PASSWORD, SECRET_KEY at minimum)
docker compose up --build
```

- Frontend: http://localhost:5173
- Backend: http://localhost:8000 (`/health`, `/api/...` per `docs/API_CONTRACT.md`)
- Postgres: localhost:5432 · Redis: localhost:6379

Apply the initial schema (until Alembic autogenerate lands):

```powershell
docker compose exec postgres psql -U onemarket -d onemarket -f /docker-entrypoint-initdb.d/0001_initial.sql
# — or from host, after mounting/copying the file:
# psql "postgresql://onemarket:<pw>@localhost:5432/onemarket" -f infra\migrations\0001_initial.sql
```

Validate compose file without a running daemon:

```powershell
docker compose config
# fallback (no docker at all):
python -c "import yaml,sys; yaml.safe_load(open('docker-compose.yml')); print('compose YAML parses OK')"
```

## Local dev (without compose)

```powershell
# backend
cd backend
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:DATABASE_URL="postgresql+psycopg://onemarket:<pw>@localhost:5432/onemarket"
$env:REDIS_URL="redis://localhost:6379/0"
uvicorn api.main:app --reload --port 8000

# frontend (new terminal)
cd frontend
npm install
npm run dev   # VITE_API_BASE_URL=http://localhost:8000
```

The app must run fully with AI keys empty (AI disabled path, §7 item 7).

## Milestone 0 acceptance (provider resilience & data quality)

- [ ] Provider failure is isolated (circuit breaker per provider, fallback to cache/secondary).
- [ ] Every response carries a complete `provenance` envelope (source, as_of, delay_minutes, quality_grade, fallback_used, missing_fields).
- [ ] Provider health dashboard live (`GET /api/providers/health` → latency p50/p95, error rate, circuit state).
- [ ] Freshness/reconciliation checks + retention rules enforced (`docs/DATA_QUALITY.md`).
- [ ] Audit logging foundation live + verifiable (see below).
- [ ] FX provenance gate enforced (no cross-market ranking until FX source is grade A/B).

## Milestone 1 acceptance (foundation + US markets)

- [ ] `docker compose up --build` brings up postgres, redis, backend, frontend.
- [ ] Search `AAPL` → Security page with price chart + source/timestamp badge.
- [ ] Cached data served on provider outage; page usable under partial failure.
- [ ] Instrument registry resolves exchange-aware identity (MIC + symbol, never bare ticker).

## Provenance + disclosure notes

- Every displayed number shows source, timestamp, delay/freshness, and quality grade.
- Missing data renders "unavailable" with reason — never zero-filled.
- Forecasts show `model_version + feature_version + data_version + timestamp`.
- AI weight capped at 20%, server-enforced; disabling AI leaves forecasting intact.
- No API key is exposed to the browser or stored in plaintext (encrypted at rest,
  decrypted only at call time, redacted in logs/audit payloads).

## Observability

- **Provider latency/error dashboard plan:** backend records per-call
  `{provider, latency_ms, ok, circuit}` to Redis stream; `GET /api/providers/health`
  aggregates p50/p95 + 1h error rate. Frontend Home page renders per-provider cards
  + global banner on any open circuit. Alert (log + audit event) on error_rate > 5%/5min.
- **Audit log verification:** `audit_logs` is append-only with `hash = sha256(prev_hash ||
  created_at || actor || action || entity || payload)`. Verify chain:
  ```powershell
  python infra\scripts\verify_audit.py  # backend agent to add; fails loudly on any gap/rewrite
  ```
  Until that script lands, spot-check ordering + hash linkage via SQL on
  `(entity_type, entity_id, id)`.
- **Backup / restore:**
  ```powershell
  # postgres backup + restore
  docker compose exec postgres pg_dump -U onemarket onemarket | Out-File -Encoding utf8 backup-$(Get-Date -Format yyyyMMdd).sql
  Get-Content backup-*.sql | docker compose exec -T postgres psql -U onemarket -d onemarket
  # redis persistence snapshot (AOF + RDB on demand)
  docker compose exec redis redis-cli BGSAVE
  docker cp onemarket-redis:/data/appendonlydir ./redis-backup/
  ```
  Backup/restore is covered by tests per spec §6 (backend agent: `test_backup_restore`).

---

## M3/M4 quickstart (forecasting + AI opinions + audit)

Deterministic forecasting works with AI keys empty. AI adds bounded opinions only.

```powershell
cd "C:\Users\thinu\OneDrive\Pictures\Documents\STOCK ANALYSIS MAIN\onemarket-analyzer"
# 1. schema (adds forecasts + audit_logs; rerun is safe)
psql "postgresql://onemarket:<pw>@localhost:5432/onemarket" -f infra\migrations\0001_initial.sql

# 2. backend with audit router (host app wires it; M3/M4 agent does NOT edit backend/api/main.py):
#    from backend.api.audit import router as audit_router
#    app.include_router(audit_router)

# 3. versioned forecast log + AI decision log
#    GET http://localhost:8000/api/audit/forecasts?symbol=AAPL
#    GET http://localhost:8000/api/audit/ai_decisions?provider=gemini

# 4. verify the audit hash chain (fails loudly on any gap/rewrite)
python -m backend.observability.audit_verify
#    $env:DATABASE_URL="postgresql+psycopg://onemarket:<pw>@localhost:5432/onemarket"
#    python -m backend.observability.audit_verify --database-url $env:DATABASE_URL

# 5. run the M3/M4 test slice
python -m pytest backend/tests/test_audit.py backend/tests/test_observability.py -q

# 6. (optional) configure Gemini for bounded opinions — server-side only, never in the browser
#    store via settings flow: put("gemini", "api_key", "<paste-once>")
#    AI-disabled path stays green: leave keys empty and /forecast still works with ai_weight=0
```

Provider latency/error dashboard data: `GET /api/providers/health`
(full dashboard payload via `backend.observability.dashboard.build_dashboard`).

## M3/M4 acceptance checklist (spec §7 items 4–6, 9)

- [ ] Deterministic forecast + calibrated confidence served (`GET .../forecast?horizon_days=21`, horizons 5/21/63 only).
- [ ] Every forecast stores `model_version + feature_version + data_version + timestamp` (versioned log at `GET /api/audit/forecasts?symbol=`).
- [ ] AI explanation + bounded opinion on explicit request only (`POST .../ai-insight`, `POST .../ai-forecast-opinion`); malformed opinions fail validation safely.
- [ ] AI weight capped at 20%, server-enforced; disabling AI leaves forecasting intact (`ai_weight=0`, `409 AI_DISABLED` on AI endpoints only).
- [ ] Provider/model switch with zero analytics/frontend changes; historical performance visible (`GET /api/ai/providers/performance`).
- [ ] Audit logs + provider health + `Not investment advice` disclosure visible (verify: `python -m backend.observability.audit_verify`).
- [ ] No plaintext keys anywhere; audit payloads redacted (covered by `test_audit.py` / `test_observability.py`).
- [ ] Docs: `docs/API_CONTRACT.md` (M3/M4 appendix), `docs/DATA_QUALITY.md` (calibration/versioning/retention), `docs/AI_PROVIDERS.md`.

## M6 acceptance checklist (SSE search/currency — spec §5 M6 + §8 search)

- [ ] Search `Moutai` / `600519` / `600519.SS` (market `SSE`) → row shows company, exchange (`XSHG`), currency (`CNY¥`), symbol (`600519.SS`).
- [ ] Market filter `All / NYSE / NASDAQ / SSE` maps to `?market=` (`XNYS` / `XNAS` / `XSHG`; All omits the param).
- [ ] No ticker ambiguity: multiple hits surface ranked candidates + `ambiguous` state; the UI never guesses.
- [ ] Market `open / closed / lunch / delayed / stale` correctly identified (`MarketStateBadge`; `lunch` = XSHG 11:30–13:00 Asia/Shanghai; `closed`/`lunch` only from explicit API value, fallback derives `open|delayed|stale` from provenance).
- [ ] Currency handling correct: `USD$` / `EUR€` / `CNY¥` via `Intl.NumberFormat` (`CurrencyValue`); every number keeps its `ProvenanceBadge`; no hardcoded live prices.
- [ ] Docs: `docs/API_CONTRACT.md` (M6 appendix), `docs/SSE_NOTES.md` (suffix/fallback/T+1/limits/lunch/holidays).

## M7 acceptance checklist (Euronext search/quote + FX-gated Watchlist — spec §5 M7–M8)

- [ ] Search `LVMH` / `MC` / `MC.PA` (market `Euronext Paris`) → row shows `MC.PA · LVMH Moet Hennessy Louis Vuitton SE · XPAR · EUR€`; `ASML.AS` (XAMS) and `UCB.BR` (XBRU) resolve to the right MIC/currency.
- [ ] Market filter `All / NYSE / NASDAQ / SSE / Euronext Paris / Euronext Amsterdam / Euronext Brussels` maps to `?market=` (`XNYS` / `XNAS` / `XSHG` / `XPAR` / `XAMS` / `XBRU`; All omits the param).
- [ ] Quote `MC.PA` (+ optional `target_ccy`) returns native `EUR` price + `market_state` (09:00–17:30 continuous, no lunch) + `ProvenanceBadge` on every number; `Intl.NumberFormat` formatting; no hardcoded prices.
- [ ] Watchlist target-ccy selector (`USD / EUR / CNY`) + `FXProvenanceBanner` (source/as_of/fallback) visible.
- [ ] Watchlist gated: fresh FX (grade A/B, delay ≤ 30m, no fallback) → ranked converted table; stale/missing FX or `FX_PROVENANCE_MISSING` → `Cross-market comparison unavailable — FX provenance missing` instead of ranked numbers (native quotes only, never rank without fresh FX).
- [ ] Docs: `docs/API_CONTRACT.md` (M7 appendix: Euronext search/quote, `/api/fx/*` + rank gate + error code), `docs/EURONEXT_NOTES.md` (suffixes/sessions/holiday stub/FX gate rules).

---

## Docs (v1)

- **User Guide** (search→brief→forecast→AI→backtest→watchlist, `AAPL`/`600519.SS`/`MC.PA`,
  FX-gate, AI-disabled mode): `docs/USER_GUIDE.md`
- **Operations** (compose, env, backup/restore, retention, health/audit-verify, secret
  rotation): `docs/OPERATIONS.md`
- **v1 Definition-of-Done checklist** (9 items with evidence + honest PARTIALs, non-goals,
  known limits): `docs/V1_CHECKLIST.md`
- Machine-check: `python scripts/verify_v1.py` (stdlib-only; exit 1 on any FAIL) ·
  `python -m py_compile scripts/verify_v1.py`

## v1 status summary (M8 docs verification)

- Backend suite: `python -m pytest backend/tests -q` → **243 passed**.
- DoD: **6 PASS** (items 1, 2, 3, 5, 6, 7) · **3 PARTIAL** (items 4, 8, 9) · **0 FAIL**.
  PARTIALs in one line: UI fetcher drift on forecast/analytics/backtest paths (+ docs-only
  contract error codes); holiday-calendar stubs (+ rank-refusal 423-vs-409 doc drift);
  missing `infra/scripts/verify_audit.py` alias + sqlite stub without `audit_logs`
  (use `python -m backend.observability.audit_verify` against Postgres).
- Non-goals hold (spec §1): no trading, no portfolio construction (placeholder only), no
  agents, no DL, no full OpenBB, no large backtest suite.
- Known limits: holiday stubs, offline stub-fallback behavior, npm blocked in this env
  (`NVM4306` — run `nvm reshim`; frontend unverified here, no `node_modules`).
- Details + evidence commands: `docs/V1_CHECKLIST.md`.
