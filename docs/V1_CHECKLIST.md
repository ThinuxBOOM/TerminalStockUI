# OneMarket Analyzer — v1 Definition-of-Done Checklist (M8)

Spec: `../markdown.md` §7 (9 items), §1 non-goals, Milestones 0–8 acceptances.
Machine-check: `python scripts/verify_v1.py` (stdlib-only; exit 1 on any FAIL).
Backend suite at sign-off: `python -m pytest backend/tests -q` → **243 passed**.
Statuses below match `verify_v1.py` output; every PARTIAL names its gap honestly.

## DoD items

### 1. Add a Gemini 3.7 Flash key securely (other providers later, no code changes) — PASS
- Evidence (files): `backend/security/secrets.py` (`EncryptedSecretStore`, Fernet over
  `SECRET_KEY`, decrypt-only-at-call-time, `redact_mapping`/`redact_string`);
  `backend/ai/providers/{gemini,openai,anthropic,xai}.py` behind one interface;
  profiles `quick_insight/forecast_assist/deep_research/report` in
  `backend/ai/prompts/__init__.py`; key policy in `docs/AI_PROVIDERS.md`.
- Evidence (command): `python -m pytest backend/tests/test_security.py backend/tests/test_ai_router.py -q`

### 2. Search and analyze NYSE/NASDAQ with full provenance — PASS
- Evidence (files): seed `AAPL` in `backend/instruments/registry.py`; router prefix
  `/api/instruments` in `backend/api/instruments.py`; `?market=` MIC filter threaded in
  `frontend/src/api/client.ts`; `MARKET_OPTIONS` + ambiguity banner in
  `frontend/src/features/search/SearchBox.tsx`; 6-field envelope in
  `backend/market_data/provenance.py`.
- Evidence (command): `python -m pytest backend/tests/test_instruments.py backend/tests/test_provenance.py -q`

### 3. Consistent charts, fundamentals, deterministic scores, data quality — PASS
- Evidence (files): `backend/analytics/{technical,fundamentals,quality,valuation}/` +
  `backend/analytics/events/timeline.py`; grade table in `docs/DATA_QUALITY.md`;
  `frontend/src/features/security/SecurityBrief.tsx` renders `ProvenanceBadge` +
  disclosure on every view; `frontend/src/features/security/PriceChart.tsx`.
- Evidence (command): `python -m pytest backend/tests/test_analytics.py backend/tests/test_analytics_api.py -q`
- Note: `npm run typecheck/build` not executable in this env (npm blocked, see Known
  limits); frontend verified by source inspection only.

### 4. Deterministic forecast + calibrated confidence — PARTIAL
- Present and tested: `ForecastService` ensemble, `backend/forecasting/calibration/`
  (Brier/ECE/reliability), horizons 5/21/63 enforced, versioned rows + disclosure,
  `ForecastDetails` inputs/evidence/versions/calibration/limitations views.
- Evidence (command): `python -m pytest backend/tests/test_forecast.py backend/tests/test_forecast_api.py backend/tests/test_backtest_api.py -q`
- Gaps (honest): (a) contract codes `400 INVALID_HORIZON` / `409 FORECAST_BLOCKED`
  (`docs/API_CONTRACT.md`) are docs-only — code raises HTTP 422 and no test asserts the
  strings; (b) UI fetcher drift — frontend calls `GET /api/forecast?symbol=..`,
  `GET /api/analytics?symbol=..`, `POST /api/backtest`, but backend serves
  `GET /api/forecast/{symbol}`, `GET /api/analytics/{symbol}`, `POST /api/backtest/run`,
  so live UI forecast/analytics/backtest degrade to the labelled placeholder /
  `unavailable` states. Backend routes are test-green; call them directly until the
  client is aligned (frontend agent owns the fix; docs agent does not touch
  `frontend/`).

### 5. AI-assisted explanation + bounded opinion (capped influence) — PASS
- Evidence (files): `AI_WEIGHT_MAX = 0.20` in `backend/ai/blend.py` + SQL
  `CHECK (ai_weight >= 0 AND ai_weight <= 0.20)` in `infra/migrations/0001_initial.sql`;
  strict schemas requiring `evidence_ids` in `backend/ai/schemas.py`;
  `CAPPED 20%` + disagreement warning in `frontend/src/components/AIOpinionCard.tsx`.
- Evidence (command): `python -m pytest backend/tests/test_ai_schemas.py backend/tests/test_ai_blend.py -q`
- Notes: contract code `AI_VALIDATION_FAILED` is docs-only (code uses HTTP 422, same
  fail-safe). UI label drift: frontend posts `Forecast Assist`, backend expects
  `forecast_assist` → UI AI requests 422; use the snake_case key directly
  (`POST /api/ai/insight`). Deterministic forecast is unaffected either way.

### 6. Switch provider/model and compare; historical performance — PASS
- Evidence (files): one `AIRouter` interface (`backend/ai/router.py`);
  `GET /api/ai/providers/performance` scoreboard by exchange × horizon;
  `frontend/src/features/providers/ProviderSettings.tsx` (+ health test).
- Evidence (command): `python -m pytest backend/tests/test_ai_router.py -q`

### 7. Entire app runs with AI completely disabled — PASS
- Evidence (files): `ai_enabled=False → weight 0 → quant passthrough`
  (`ai_disabled_forecast` in `backend/ai/blend.py`); AI-disabled e2e test in
  `backend/tests/test_e2e_journey.py`; AI-disabled policy in `docs/AI_PROVIDERS.md`.
- Evidence (command): `python -m pytest backend/tests/test_ai_blend.py backend/tests/test_e2e_journey.py -q`
- Note: contract code `AI_DISABLED` is docs-only; disabled behavior (weight-0 blend,
  AI-endpoints-only refusal, `/forecast` intact) is implemented and tested.

### 8. SSE, then Euronext, under the same reliability bar — PARTIAL
- Present and tested: Yahoo suffixes `.SS/.PA/.AS/.BR` (`backend/instruments/calendars.py`),
  seeds `600519/MC/ASML/UCB` (`backend/instruments/registry.py`),
  `sse-drift-v1` / `eux-drift-v1` + `sse-features-v1` / `eux-features-v1`,
  FX gate in both layers + `Cross-market comparison unavailable — FX provenance missing`
  gate panel (`frontend/src/pages/WatchlistPage.tsx`); `docs/SSE_NOTES.md`,
  `docs/EURONEXT_NOTES.md`, M6/M7 appendices in `docs/API_CONTRACT.md`.
- Evidence (command): `python -m pytest backend/tests/test_sse_instruments.py backend/tests/test_euronext_instruments.py backend/tests/test_fx.py -q`
- Gaps (honest): (a) holiday calendars are **stubs** (`is_holiday`/`XSHG_HOLIDAY_STUB`,
  six-feast Euronext stub) — outside sessions `market_state` may be
  provenance-derived `delayed`/`stale` instead of calendar-exact `closed`; production
  must use licensed calendars; (b) rank-refusal status drift — code returns HTTP 423,
  `docs/API_CONTRACT.md` M7 appendix says 409 (same `FX_PROVENANCE_MISSING` code).

### 9. Audit logs, provider health, “not investment advice” disclosures — PARTIAL
- Present and tested: hash-chained audit log + `python -m backend.observability.audit_verify`;
  versioned `GET /api/audit/forecasts` + `GET /api/audit/ai_decisions` (payloads redacted);
  `GET /api/providers/health` dashboard + safe `POST /api/providers/health/test`;
  disclosure string in backend (`backend/ai/schemas.py` DISCLAIMER) and on every forecast
  view (Brief, Forecast Details, Backtest Lab).
- Evidence (command): `python -m backend.observability.audit_verify` +
  `python -m pytest backend/tests/test_audit.py backend/tests/test_observability.py -q`
- Gaps (honest): (a) `README.md` references `infra/scripts/verify_audit.py`, which does
  not exist — use the `python -m` command above; (b) local `./onemarket.db` sqlite stub
  has no `audit_logs` table (postgres migration `infra/migrations/0001_initial.sql` not
  applied to it); point the verifier at Postgres after applying the migration
  (`docs/OPERATIONS.md` §5).

## Milestones 0–8 acceptance rollup

- M0 resilience/quality: PASS (per-provider breakers, provenance everywhere, health
  dashboard, retention rules, audit foundation, FX gate) — modulo item-9 gaps above.
- M1 foundation/US: PASS (compose brings up 4 services; `AAPL` search→Brief; cached
  fallback; MIC-aware identity).
- M2 analytics: PASS (formula+fixtures+docs per metric; `unavailable` semantics).
- M3 forecasting: PARTIAL (engine+calibration+versioning green; UI fetcher + contract-code
  drift per item 4).
- M4 provider framework: PASS (Gemini end-to-end path, safe validation failure, zero-change
  swap) — modulo item-5 UI label drift.
- M5 bounded prediction: PASS (20% cap server-enforced; disable-intact; disagreement
  lowers confidence; performance scoreboard).
- M6 SSE: PARTIAL (search/currency/market-state green; holiday-stub limit per item 8).
- M7 Euronext + FX gate: PARTIAL (same bar as SSE; gate enforced; stub + 423/409 drift
  per item 8).
- M8 UX polish: PARTIAL (all 8 pages present with provenance/disclosure/gating; live
  forecast/analytics/backtest UI calls affected by item-4 fetcher drift; graceful
  degraded states everywhere).

## Non-goals reaffirmed (spec §1)

No live trading, no portfolio construction (`frontend/src/features/portfolio/` is an
explicit placeholder), no autonomous agents, no deep learning, no full OpenBB dependency,
no large backtesting suite. Nothing in v1 contradicts these.

## Known limits

1. **Holiday stubs** — `backend/instruments/calendars.py`: XSHG lunar approximations +
   six-feast Euronext stub; `open` means “fresh within expected delay”, never a trading
   signal; licensed calendars required for production.
2. **Stub fallback offline** — with no network, FX falls back to the flagged reference
   stub (`fallback_used: true`, grade C) and quotes degrade to cached/stale with honest
   badges; ranking stays gated by design.
3. **npm blocked in this env** — `npm` fails with `NVM4306` (delegated script identity
   changed; fix: `nvm reshim` / `nvm doctor --autofix`; node v22.23.2 itself runs).
   Frontend `typecheck`/`build`/`dev` therefore unverified here; no `node_modules`
   present. Backend is fully verified (243 passed).
4. **Contract/code drifts** (docs-side fixes proposed, code owned by other agents):
   rank-refusal 423-vs-409, horizon errors 422-vs-400, docs-only code strings
   (`INVALID_HORIZON`, `FORECAST_BLOCKED`, `AI_DISABLED`, `AI_VALIDATION_FAILED`),
   securities-scoped AI paths in the M3/M4 appendix vs implemented `/api/ai/*`,
   frontend fetcher paths + AI profile labels (items 4/5).
