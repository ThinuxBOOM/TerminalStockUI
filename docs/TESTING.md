# Testing (M8 — E2E + reliability)

How to run the suites, what the failure-simulation matrix covers, and which
checks guard keys, leakage, and audit integrity.

## 1. Backend (pytest)

From repo root (`onemarket-analyzer/`):

```bash
# Targeted M8 suites
pytest backend/tests/test_e2e_journey.py \
       backend/tests/test_failure_modes.py \
       backend/tests/test_security_redaction.py \
       backend/tests/test_load_smoke.py -q

# Full suite (no regressions gate)
pytest backend/tests -q
```

All backend tests use `TestClient` with stub market data — **no network**.
The journey/failure/load suites inject a stub-mode `YFinanceProvider` into
both the FastAPI dependency graph and the `deps` singleton (the AI packet
builder reads the singleton directly), and use an empty
`EncryptedSecretStore` so AI calls take the marked-stub path.

## 2. Frontend (Playwright)

```bash
cd frontend
npm i -D @playwright/test      # one-time (not in package.json yet)
npm run dev &                  # serves http://localhost:5173
npx playwright test -c e2e/playwright.config.ts
```

- Config: `frontend/e2e/playwright.config.ts` (`baseURL http://localhost:5173`,
  backend override via `API_BASE_URL`, frontend via `FRONTEND_BASE_URL`).
- Flow: `frontend/e2e/journey.spec.ts` — Search → Security Brief → AI Insight.
- **Skip-friendly:** each test probes `$API/health` first and calls
  `test.skip()` when the backend is offline, so the run stays green on a
  frontend-only checkout.
- Selector convention is documented at the top of the spec (accessible
  roles/text today; `data-testid="search-result-<SYM>"`,
  `"security-brief"`, `"ai-insight-request"`, `"ai-opinion-card"` proposed
  once `frontend/src/*` is next touched).

## 3. Load smoke

Covered by `backend/tests/test_load_smoke.py` (runs inside pytest, no extra
infra):

| Check | Budget | Notes |
|---|---|---|
| 50 sequential `quote` + `forecast` TestClient calls | < 30 s | documents throughput; **not** a hard SLO/perf gate |
| 6-symbol watchlist (`AAPL MSFT NVDA 600519.SS MC.PA ASML.AS`) ×4, concurrent via threads | < 30 s | one `TestClient` per thread, shared stub app |

For real HTTP load later: `locust -f infra/load/locustfile.py --headless -u 20 -r 5`
(stub to be added) or a k6 script against `/api/market_data/quote` +
`/api/forecast/{symbol}`; keep the same <30 s smoke budgets as a starting SLO.

## 4. Failure-simulation matrix

(`backend/tests/test_failure_modes.py`)

| Simulated failure | Expected behavior |
|---|---|
| Provider outage (`_fetch_raw` raises) | Service raises `ProviderError`; HTTP 502, never 200+stale/fallback |
| Circuit breaker open | Service raises fail-closed (provider-level flagged stub is refused by the service); HTTP 502 |
| yfinance down, SSE request | served live via AKShare (`source=akshare`, `CNY`, `fallback_used=false`); breakers are per-provider objects — one open never trips the other |
| Invalid horizon (`7`, `7d`, `30`, `soon`) on forecast / backtest / AI opinion; unknown AI profile | 422, never 500 |
| Malformed AI JSON (empty, truncated, bad prob/horizon, missing `evidence_ids`) | `parse_opinion_strict` raises `ValueError`; API returns 502/423, never a partial opinion on the wire |
| FX stale (>24 h) or missing on `POST /api/fx/rank` | 423 + `code FX_PROVENANCE_MISSING` (no `allow_fallback` escape — fail-closed) |
| Unknown symbol: `resolve` / `instruments/{id}` / `securities/{id}/quote` | 404; audit `forecasts` returns empty 200; backtest history / quote with no live data → 502; bad-symbol shape (e.g. underscore) → 422; empty quote symbol is 422 — **never 500, never stub 200** |
| AI insight/forecast_opinion with no key | 423 AI disabled (deterministic forecast unaffected); live-call failure → 502 (stub refused on wire) |

## 5. Leakage / key / audit checks

- **Keys:** `test_security_redaction.py` plants `sk-live-*` secrets, then scans
  insight / forecast / audit / AI-performance / AI-health JSON — secrets must
  appear nowhere. `describe()` / `health()` expose names + `configured` only.
  `test_audit.py` additionally proves smuggled keys never persist in audit rows.
- **Forecast leakage:** `test_forecast_api.py` + `test_backtest_api.py` assert the
  `assert_no_leakage` walk-forward guard (overlap/gap violations raise) and
  past-only feature stability.
- **Audit:** `test_audit.py` verifies the hash chain (`verify_chain` ok on
  append, detects rewrites/gaps), forecast versioning
  (model/feature/data version + timestamp per row), and the
  `forecasts` / `ai_decisions` endpoints (redacted, weight surfaced,
  `Not investment advice` disclosure).
- **Provenance:** `test_provenance.py` + the E2E journey assert the full
  `{source, as_of, delay_minutes, quality_grade, fallback_used,
  missing_fields}` envelope on search / quote / forecast / analytics /
  backtest responses.
