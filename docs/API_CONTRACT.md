# OneMarket Analyzer — REST contract (v1 draft, infra-owned stub)

Spec: §§2–4. Deterministic analytics own every number. AI adds **bounded opinion only**.
Backend agent owns implementation; this file is the cross-agent contract.

## Conventions

- Base URL (compose): `http://localhost:8000`. Frontend uses `VITE_API_BASE_URL`.
- Every data-bearing response includes a `provenance` envelope (§4). No exceptions.
- Timestamps are ISO-8601 UTC. Money carries explicit `currency`.
- `Not investment advice` disclosure string is returned on every forecast endpoint
  and must be rendered on every forecast view.

### Provenance envelope (required on all market data / analytics / forecast payloads)

```json
{
  "source": "yfinance",
  "as_of": "2026-09-12T14:30:00Z",
  "delay_minutes": 15,
  "quality_grade": "B",
  "fallback_used": false,
  "missing_fields": []
}
```

| Field | Meaning |
|---|---|
| `source` | Upstream provider that actually served the data |
| `as_of` | Upstream timestamp the data is current as of |
| `delay_minutes` | Known feed delay (free/delayed sources must be honest) |
| `quality_grade` | A/B/C/D per `docs/DATA_QUALITY.md` |
| `fallback_used` | True when served from cache/secondary after primary failure |
| `missing_fields` | Fields requested but unavailable (drives "unavailable" UI, never NaN-as-zero) |

## Endpoints

### `GET /health`
Liveness + dependency status. Used by compose healthcheck and provider dashboard.
```json
{"status": "ok", "postgres": "up", "redis": "up", "version": "0.1.0"}
```

### `GET /api/instruments/search?q=AAPL&market=XNAS&limit=10`
Exchange-aware search. Never resolve by bare ticker alone.
- Response: list of canonical instruments (§4): `instrument_id, exchange_mic,
  exchange_symbol, company_name, currency, country, sector, is_active` + `provenance`.

### `GET /api/securities/{instrument_id}/quote`
Latest quote + freshness badge data. Includes `provenance`, `price`, `currency`,
`change`, `market_state` (`open|closed|delayed|stale`).

### `GET /api/securities/{instrument_id}/bars?timeframe=1d&from=...&to=...`
Corporate-action-adjusted OHLCV. Each bar carries `source/as_of/quality_grade`
or a shared `provenance` block + per-bar `missing_fields` when sparse.

### `GET /api/securities/{instrument_id}/analytics`
Deterministic analytics (Milestone 2): technical, fundamentals, quality
(Piotroski/Altman-Z/Beneish-M/DuPont), valuation. Every metric:
`{ "value": ..., "formula": "...", "inputs": [...], "provenance": {...} }`.
Missing data → `{"value": null, "status": "unavailable", "reason": "..."}`.

### `GET /api/securities/{instrument_id}/forecast?horizon_days=21`
Deterministic forecast (Milestone 3). Measurable probabilities, not advice.
```json
{
  "instrument_id": "...",
  "horizon_days": 21,
  "direction_probability": 0.64,
  "expected_return_range": [-0.04, 0.09],
  "volatility_regime": "elevated",
  "drawdown_probability": 0.18,
  "confidence": "moderate",
  "model_version": "gbm-us-v0.1.0",
  "feature_version": "feat-us-v0.1.0",
  "data_version": "bars-2026-09-12",
  "disclosure": "Not investment advice. For informational purposes only.",
  "provenance": {"source": "deterministic-engine", "as_of": "...", "delay_minutes": 0, "quality_grade": "A", "fallback_used": false, "missing_fields": []}
}
```

### `POST /api/securities/{instrument_id}/ai-insight`
Explicit-request-only AI call (Milestone 4/5). Body: `{ "profile": "quick_insight|forecast_assist|deep_research|report", "provider": "gemini|openai|anthropic|xai", "model": "..." }`.
- Implemented routes are `POST /api/ai/insight {symbol, profile}` (`backend/api/ai.py:169-186`)
  and `POST /api/ai/forecast_opinion {symbol, horizon[, profile, quant_prob, ai_weight, ai_enabled]}`
  (`backend/api/ai.py:189-225`); the `/api/securities/.../ai-*` paths above are the
  pre-implementation sketch and are NOT served (see Phase 4a table below).
- Returns **strict-schema JSON** bounded opinion: `direction, probability (0–1),
  time_horizon_days (5|21|63 only), catalysts[], risks[], evidence_ids[], limitations[]`.
- Rejects claims without `evidence_ids`; rejects out-of-range probabilities/horizons.
- `ai_weight ≤ 0.20`, server-enforced. Disabling AI leaves `/forecast` intact.
- API keys never accepted from, or returned to, the client.
- Profile keys: server normalizes (`backend/api/ai.py:72-76`
  `key = (profile or "").strip().lower().replace(" ", "_").replace("-", "_")`;
  accepted `PROFILES = ("quick_insight", "forecast_assist", "deep_research", "report")`
  in `backend/ai/prompts/__init__.py:23`), so display labels (`Quick Insight`,
  `Deep Research`, `Forecast Assist`, `Report` sent by `frontend/src/api/client.ts:290-296`
  via `postAIInsight` at `frontend/src/api/client.ts:666-673`) are accepted and mapped
  to snake_case. Unknown profiles → `422 {"detail": "unknown AI profile: ...; expected one of
  ['quick_insight', 'forecast_assist', 'deep_research', 'report']"}`.

### `GET /api/providers/health`
Per-provider latency/error/circuit state for the dashboard (Milestone 0):
`{ "provider": "yfinance", "latency_p50_ms": 320, "error_rate_1h": 0.01, "circuit": "closed", "last_check": "..." }`.

### `GET /api/audit?entity_type=forecast&entity_id=...`
Paginated audit trail for forecasts and AI decisions. Payloads have secrets redacted.
Supports hash-chain verification (see README § Observability).

## Error shape
```json
{"error": {"code": "PROVIDER_UNAVAILABLE", "message": "yfinance timed out; served cached bars", "retryable": true, "provenance": {...}}}
```
Partial failure must still return usable cached data with `fallback_used: true` (M1 acceptance).

---

## M3/M4 appendix (forecasting + AI + audit — normative)

Spec §§5 (M3–M5), §6 testing, §7 definition of done. Deterministic
endpoints are the source of truth; AI endpoints return **bounded opinion
only** and never override the quantitative core.

### `GET /api/securities/{instrument_id}/forecast?horizon_days=21` (M3)

- Implemented route is `GET /api/forecast/{symbol}?horizon=5|21|63`
  (`backend/api/forecast.py:163-168`). Query key is `horizon` (not `horizon_days`).
- Horizons outside `{5, 21, 63}` → `422` (not `400`) with
  `{"detail": "horizon must be one of [5, 21, 63], got <value>"}` exactly
  (`backend/api/forecast.py:170-174`; `FORECAST_HORIZONS = (5, 21, 63)` in
  `backend/forecasting/common.py:13`). The `400 INVALID_HORIZON` code in earlier
  drafts is docs-only — no such `error.code` is emitted by code (grep over
  `backend/` finds no `INVALID_HORIZON`).
- Response: same shape as the `GET .../forecast` example above, plus:
  - `model_version`, `feature_version`, `data_version` (all required, non-empty).
  - `created_at` (row timestamp), `target_date` (as_of + horizon).
  - `ai_provider / ai_model / ai_weight` (`ai_weight` ∈ `[0, 0.20]`, `0` when AI disabled).
  - `disclosure: "Not investment advice. For informational purposes only."`
  - `provenance` envelope (required).
- Every call persists a versioned row to `forecasts`
  (`UNIQUE (instrument_id, horizon_days, target_date, model_version,
  feature_version, data_version)`) and emits `forecast.created` to the audit log.
- Grade D data → docs-draft `409 FORECAST_BLOCKED` is docs-only — no such
  `error.code` is emitted by code (grep over `backend/` finds no `FORECAST_BLOCKED`).
  Unavailable semantics (`{"value": null, "status": "unavailable"}`) are preserved
  per-metric by the analytics modules; forecasts never fabricate.

### `GET /api/securities/{instrument_id}/analytics` (M2, referenced by M3/M4)

- Sections: `technical, fundamentals, quality, valuation, events`.
- Every metric: `{value, formula, inputs, provenance}`; missing → `{value: null, status: "unavailable", reason}`.

### `GET /api/securities/{instrument_id}/backtest?horizon_days=21` (M3, lightweight)

- Walk-forward only, time-ordered splits, corporate-action-adjusted prices.
- Response: `{instrument_id, horizon_days, folds, brier_score, calibration_error, reliability_table, model_version, feature_version, data_version, provenance}`.
- Failure folds are reported, never hidden (M3 acceptance: dashboard shows failures too).

### `POST /api/securities/{instrument_id}/ai-insight` (M4/M5)

- Implemented as `POST /api/ai/insight` (`backend/api/ai.py:169-186`); the
  securities-scoped path is the pre-implementation sketch, not a served route.
- Explicit-request only. Body: `{symbol, profile}` where `profile` accepts
  `quick_insight|forecast_assist|deep_research|report` AND the display-label forms
  (`Quick Insight`, `Deep Research`, `Forecast Assist`, `Report`) via server-side
  normalization (`backend/api/ai.py:72-76`). Unknown profile → `422`
  `{"detail": "unknown AI profile: ...; expected one of [...]"}`.
- Returns strict-schema bounded opinion: `direction (bullish|bearish|neutral), probability (0–1), time_horizon_days (5|21|63 only), catalysts[], risks[], evidence_ids[] (non-empty), limitations[]`.
- Server-enforced: reject claims without `evidence_ids`; reject out-of-range probabilities/horizons; `ai_weight ≤ 0.20`.
- Keys: never accepted from, or returned to, the client. Request/response payloads are redacted in logs and audit rows.

### `POST /api/securities/{instrument_id}/ai-forecast-opinion` (M5, bounded)

- Implemented as `POST /api/ai/forecast_opinion` (`backend/api/ai.py:189-225`).
- Same profile validation as `ai-insight` with `forecast_assist` profile semantics: may raise/lower/leave unchanged the displayed confidence but never overrides the deterministic forecast. Disabling AI leaves `/forecast` intact.
- Horizon validation: `backend/api/ai.py:79-97` (`_check_horizon`) — any non-`5|21|63`
  value (bool, non-integer float, non-numeric string, out-of-range int) → `422`
  `{"detail": "horizon must be one of 5, 21, 63"}` exactly. `quant_prob` outside
  `[0, 1]` → `422 {"detail": "quant_prob must be in [0, 1]"}` (`backend/api/ai.py:195-196`).
  Bad `ai_weight` → `422` with the `ValueError` text from `resolve_ai_weight`
  (`backend/api/ai.py:197-200`; presets/cap in `backend/ai/blend.py:17-46`).

### `GET /api/ai/providers/performance?exchange=XNAS&horizon=21` (M5)

- Implemented route `GET /api/ai/providers/performance[?exchange&horizon]`
  (`backend/api/ai.py:228-236`). Query keys are `exchange` and `horizon` (not
  `horizon_days`); non-`5|21|63` `horizon` → `422 {"detail": "horizon must be one of 5, 21, 63"}`
  (`backend/api/ai.py:233-234`).
- Historical provider/model scoreboard by exchange and horizon: `{rows, disclaimer}` where
  each row is `{provider, model, exchange, horizon, calls, stub_calls, errors, decided,
  accuracy, stub_rate, error_rate, avg_latency_ms}` (`backend/ai/router.py:100-114`).
- Used to justify the fixed 20% cap and any future learned weighting (only after sufficient out-of-sample evidence).

### `POST /api/ai/providers/health/test` and `POST /api/providers/health/test`

- `POST /api/ai/providers/health/test [{provider}]` (`backend/api/ai.py:239-249`):
  unknown provider → `422 {"detail": "unknown provider: ..."}`; returns `{providers: [...]}` (config only, never key material).
- `POST /api/providers/health/test[?provider=yfinance]` (`backend/api/providers.py:26-34`):
  fetches a reference `AAPL` quote, returns fresh tracker stats.

### `GET /api/audit/forecasts?symbol=AAPL&horizon_days=21&limit=50&offset=0` (M0/M3, implemented in `backend/api/audit.py`)

- Versioned forecast log. Optional filters: `symbol` (case-insensitive exchange symbol), `instrument_id` (UUID), `horizon_days`.
- Response: `{forecasts: [{forecast_id, instrument_id, symbol, horizon_days, target_date, direction_probability, expected_return_range, volatility_regime, drawdown_probability, confidence, model_version, feature_version, data_version, ai_provider, ai_model, ai_weight, provenance, created_at}], count, limit, offset, disclosure}`.

### `GET /api/audit/ai_decisions?provider=gemini&limit=50&offset=0` (M4/M5, implemented in `backend/api/audit.py`)

- AI opinion log. Each row: `{id, created_at, actor, action, entity_type, entity_id, provider, model, weight, evidence_ids, payload (redacted), prev_hash, hash}`.
- `payload` never contains keys (redacted pre-insert via `redact_mapping` and re-redacted on read).
- Wire-up (no edit to `backend/api/main.py` by the M3/M4 agent — host app adds):
  ```python
  from backend.api.audit import router as audit_router
  app.include_router(audit_router)
  ```

### Error codes (all endpoints — code truth, Phase 4a)

| `error.code` / `detail` | HTTP | Meaning | Source |
|---|---|---|---|
| (no code; `detail: "horizon must be one of [5, 21, 63], got ..."`) | 422 | `horizon` not in `{5, 21, 63}` on forecast/screener | `backend/api/forecast.py:170-174`, `backend/api/screener.py:82-86` |
| (no code; `detail: "horizon must be one of 5, 21, 63"`) | 422 | bad AI horizon on `/api/ai/*` | `backend/api/ai.py:79-97,233-234` |
| (no code; `detail: "unknown AI profile: ...; expected one of [...]"`) | 422 | unknown AI profile | `backend/api/ai.py:72-76` |
| (no code; `detail: "unknown instrument_id"`) | 404 | `instrument_id` not in registry/DB | `backend/api/market_data.py:74-76,88-90` |
| (no code; `detail: "unknown alert ..."` ) | 404 | `alert_id` not found / unparseable UUID | `backend/api/alerts.py:134-155` |
| `INVALID_HORIZON` | — (docs-only) | Earlier drafts claimed `400`; code emits `422` with the strings above, no `error.code` | grep `backend/` finds no `INVALID_HORIZON` |
| `UNKNOWN_INSTRUMENT` | — (docs-only; note pre-existing typo) | Draft code string; code emits `404 {"detail": "unknown instrument_id"}` | `backend/api/market_data.py:74-76` |
| `FORECAST_BLOCKED` | — (docs-only) | Draft `409`; no such code in code | grep `backend/` finds no `FORECAST_BLOCKED` |
| `AI_VALIDATION_FAILED` | — (docs-only) | Draft `422` code; code uses bare `422 {"detail": ...}` with fail-safe stub/degrade, never this string | grep `backend/` finds no `AI_VALIDATION_FAILED`; see `backend/ai/router.py:170-216` |
| `AI_DISABLED` | — (docs-only) | Draft `409`; disabled behavior is weight-0 blend (`ai_enabled=False → 0.0`, `backend/ai/blend.py:23-32,87-99`) with `/forecast` intact, never this code | grep `backend/` finds no `AI_DISABLED` |
| `PROVIDER_UNAVAILABLE` | 502 | Upstream failed; cached `fallback_used: true` payload when available (`detail: str(exc)` from `ProviderError`) | `backend/api/market_data.py:54-55`, `backend/api/fx.py:147-149,174-176,231-233` |
| `RATE_LIMITED` | 429 | Provider/token-bucket budget exhausted; `retryable: true` | (retained draft semantics; no dedicated backend symbol in Phase 4a scope) |
| `FX_PROVENANCE_MISSING` | 423 (NOT 409) | FX provenance stale/missing/fallback-without-opt-in; `/api/fx/rank` refused | `backend/market_data/fx/convert.py:23`, `backend/api/fx.py:243-254` |

### Disclosure note (required)

- Every forecast-bearing response includes `"disclosure": "Not investment advice. For informational purposes only."`.
- Every forecast view renders that string verbatim. AI opinions additionally render `ai_weight` and `limitations[]`.

---

## M6 appendix (SSE — normative)

Spec §5 Milestone 6. Exchange-aware SSE support (XSHG, Yahoo `.SS` suffix, AKShare fallback).

### Search: `GET /api/instruments/search?q=Moutai&market=XSHG`

- `market` is a MIC filter: `XNYS | XNAS | XSHG` (M6 frontend exposes All/NYSE/NASDAQ/SSE). Omit or `ALL` for cross-market.
- Name search works: `q=Moutai` matches `company_name` `"Kweichow Moutai Co., Ltd."` (case-insensitive substring, ranked after exact/prefix symbol hits).
- Symbol-tolerant: `q=600519` and `q=600519.SS` both resolve to the same instrument (`exchange_symbol` vs `provider_symbol`); display always uses the provider (Yahoo-style) form `600519.SS`.
- Ambiguity is surfaced, never guessed: multiple hits return the ranked list; `/api/instruments/resolve` and `/api/market_data/quote` set `ambiguous: true` + `candidates[]` when applicable.

Example (truncated):

```json
{
  "query": "Moutai",
  "market": "XSHG",
  "results": [
    {
      "instrument_id": "XSHG-600519.SS",
      "exchange_mic": "XSHG",
      "exchange_symbol": "600519.SS",
      "provider_symbol": "600519.SS",
      "company_name": "Kweichow Moutai Co., Ltd.",
      "currency": "CNY",
      "country": "CN",
      "sector": "Consumer Defensive"
    }
  ],
  "provenance": {
    "source": "instrument-registry",
    "as_of": "2026-09-12T14:30:00Z",
    "delay_minutes": 0,
    "quality_grade": "A",
    "fallback_used": false,
    "missing_fields": []
  }
}
```

### Quote: `GET /api/market_data/quote?symbol=600519.SS&market=XSHG`

Example (truncated):

```json
{
  "symbol": "600519.SS",
  "instrument": {
    "instrument_id": "XSHG-600519.SS",
    "exchange_mic": "XSHG",
    "exchange_symbol": "600519.SS",
    "provider_symbol": "600519.SS",
    "company_name": "Kweichow Moutai Co., Ltd.",
    "currency": "CNY"
  },
  "price": 1880.5,
  "currency": "CNY",
  "market_state": "lunch",
  "ambiguous": false,
  "candidates": [],
  "provenance": {
    "source": "yfinance",
    "as_of": "2026-09-12T06:30:00Z",
    "delay_minutes": 15,
    "quality_grade": "B",
    "fallback_used": false,
    "missing_fields": []
  }
}
```

### `market_state` values

`open | closed | lunch | delayed | stale`

- `lunch` is the SSE midday break (11:30–13:00 Asia/Shanghai) and is only ever sent explicitly by the backend (exchange calendar). The frontend never synthesizes `closed`/`lunch` — fallback derivation from provenance yields only `open | delayed | stale`.
- Frontend fallback: `delay_minutes <= 1` → `open`; `<= 30` (or cached fallback) → `delayed`; otherwise `stale`; `-1` / missing → `stale`.

### CNY note

- SSE quotes carry `"currency": "CNY"`. The frontend formats via `Intl.NumberFormat` (`zh-CN` / `CNY` → `¥`; `en-US` / `USD` → `$`; `de-DE` / `EUR` → `€`), never hardcodes prices or FX.
- No cross-market conversion or ranking without FX provenance (M0 gate still applies).

---

## M7 appendix (Euronext — normative)

Spec §5 Milestones 7–8. Euronext Paris (XPAR, `.PA`), Amsterdam (XAMS,
`.AS`), Brussels (XBRU, `.BR`) at the same quality bar as US/SSE.
Cross-market comparison ONLY after FX provenance is solid.

### Search: `GET /api/instruments/search?q=MC&market=XPAR`

- `market` is a MIC filter: `XNYS | XNAS | XSHG | XPAR | XAMS | XBRU`.
  Omit or `ALL` for cross-market. Frontend exposes All / NYSE / NASDAQ /
  SSE / Euronext Paris / Euronext Amsterdam / Euronext Brussels.
- Name + symbol tolerant: `q=LVMH`, `q=MC`, and `q=MC.PA` all resolve to
  the same instrument (display always uses the provider form `MC.PA`);
  `q=ASML` / `q=ASML.AS` → `ASML.AS` (XAMS); `q=UCB` / `q=UCB.BR` →
  `UCB.BR` (XBRU).
- Ambiguity is surfaced, never guessed (ranked list + `ambiguous` /
  `candidates[]`).

Example (truncated):

```json
{
  "query": "MC",
  "market": "XPAR",
  "results": [
    {
      "instrument_id": "XPAR-MC.PA",
      "exchange_mic": "XPAR",
      "exchange_symbol": "MC.PA",
      "provider_symbol": "MC.PA",
      "company_name": "LVMH Moet Hennessy Louis Vuitton SE",
      "currency": "EUR",
      "country": "FR",
      "sector": "Consumer Cyclical"
    }
  ],
  "provenance": {
    "source": "instrument-registry",
    "as_of": "2026-09-12T14:30:00Z",
    "delay_minutes": 0,
    "quality_grade": "A",
    "fallback_used": false,
    "missing_fields": []
  }
}
```

### Quote: `GET /api/market_data/quote?symbol=MC.PA&market=XPAR&target_ccy=USD`

- `market` scopes identity (`XPAR` for `MC.PA`); `target_ccy` (optional:
  `USD | EUR | CNY`) asks the backend for a converted preview. The
  authority for conversion/ranking stays with `/api/fx/*` — the quote
  `price` remains native-currency (`EUR` for Euronext).
- Frontend threads `target_ccy` via `getQuote(symbol, market, targetCcy)`;
  backends that ignore it remain compatible (client tolerates absence).

Example (truncated):

```json
{
  "symbol": "MC.PA",
  "instrument": {
    "instrument_id": "XPAR-MC.PA",
    "exchange_mic": "XPAR",
    "exchange_symbol": "MC.PA",
    "provider_symbol": "MC.PA",
    "company_name": "LVMH Moet Hennessy Louis Vuitton SE",
    "currency": "EUR"
  },
  "price": 715.5,
  "currency": "EUR",
  "market_state": "open",
  "ambiguous": false,
  "candidates": [],
  "provenance": {
    "source": "yfinance",
    "as_of": "2026-09-12T14:30:00Z",
    "delay_minutes": 15,
    "quality_grade": "B",
    "fallback_used": false,
    "missing_fields": []
  }
}
```

### Euronext sessions

- Continuous 09:00–17:30 exchange-local (Europe/Paris, Europe/Amsterdam,
  Europe/Brussels). No lunch break (unlike XSHG). `market_state` is
  `open | closed | delayed | stale` from the exchange calendar layered over
  provenance freshness; frontend never synthesizes `closed`/`lunch`.

### FX: `GET /api/fx/rate?base=EUR&quote=USD`

```json
{
  "base": "EUR",
  "quote": "USD",
  "rate": 1.0852,
  "provenance": {
    "source": "fx-feed",
    "as_of": "2026-09-12T14:30:00Z",
    "delay_minutes": 5,
    "quality_grade": "A",
    "fallback_used": false,
    "missing_fields": []
  }
}
```

### FX: `POST /api/fx/convert` `{ "amount": 100, "from": "EUR", "to": "USD" }`

```json
{
  "amount": 100,
  "from": "EUR",
  "to": "USD",
  "converted": 108.52,
  "rate": 1.0852,
  "provenance": {
    "source": "fx-feed",
    "as_of": "2026-09-12T14:30:00Z",
    "delay_minutes": 5,
    "quality_grade": "A",
    "fallback_used": false,
    "missing_fields": []
  }
}
```

### FX: `POST /api/fx/rank` `{ "symbols": ["AAPL", "MC.PA", "ASML.AS"], "target_ccy": "USD" }`

- Converts each symbol to `target_ccy` and returns the ranking. Every row
  carries its quote `provenance`; the response carries top-level
  `fx_provenance` for the FX feed.
- **Gate:** ranking requires fresh FX provenance (grade A/B, `delay_minutes`
  0–30, `fallback_used: false`). Otherwise the backend MUST refuse with
  `FX_PROVENANCE_MISSING` and the frontend MUST render
  `Cross-market comparison unavailable — FX provenance missing` instead of
  ranked numbers (never rank without fresh FX).

Success (truncated):

```json
{
  "target_ccy": "USD",
  "ranking": [
    {
      "symbol": "MC.PA",
      "price": 715.5,
      "currency": "EUR",
      "converted_price": 776.46,
      "target_ccy": "USD",
      "instrument": {"exchange_mic": "XPAR", "provider_symbol": "MC.PA"},
      "provenance": {"source": "yfinance", "as_of": "...", "delay_minutes": 15, "quality_grade": "B", "fallback_used": false, "missing_fields": []}
    }
  ],
  "fx_provenance": {
    "source": "fx-feed",
    "as_of": "2026-09-12T14:30:00Z",
    "delay_minutes": 5,
    "quality_grade": "A",
    "fallback_used": false,
    "missing_fields": []
  }
}
```

Gate refusal (HTTP 423 — `backend/api/fx.py:244-254`):

```json
{"error": {"code": "FX_PROVENANCE_MISSING", "message": "FX feed stale/missing; ranking refused", "retryable": true, "provenance": {...}}}
```

Note: the wire `message` is the gate exception text from
`backend/market_data/fx/convert.py:103-123` (e.g. `"FX provenance missing: ..."`,
`"FX rates stale: as_of is ...h old (limit 24h); ..."`, or
`"FX rates are fallback (ECB reference stub): pass allow_fallback=True ..."`),
not the abbreviated sketch above. Gate rules: missing/unparseable `as_of` → refuse;
`as_of` older than 24h (`MAX_AGE_HOURS = 24.0`, `backend/market_data/fx/convert.py:24`) →
refuse; `fallback_used: true` without explicit `allow_fallback=true` in
`POST /api/fx/rank {symbols, target_ccy, allow_fallback=false}` (`backend/api/fx.py:68-71,240-242`) → refuse.

### Error codes (M7 addition — corrected Phase 4a)

| `error.code` | HTTP | Meaning |
|---|---|---|
| `FX_PROVENANCE_MISSING` | 423 (NOT 409 — earlier drafts said 409; code returns `JSONResponse(status_code=423, ...)` at `backend/api/fx.py:244-254` with `CODE = "FX_PROVENANCE_MISSING"` from `backend/market_data/fx/convert.py:23`) | FX provenance stale/missing; `/api/fx/rank` (and convert when applicable) refused. Client shows the gate message and native-currency quotes only. |

---

## Phase 4a appendix (implemented fetcher paths + new surfaces — normative)

Code wins over all earlier sketches. Every row below was verified against the
router cited; frontend alignment notes cite `frontend/src/api/client.ts`.

### Fetcher paths table (all current routes)

| Client call | HTTP | Backend route | Source |
|---|---|---|---|
| `getForecast(symbol, horizon)` path-style (primary) | `GET /api/forecast/{symbol}?horizon=5\|21\|63` | `backend/api/forecast.py:163-168` | `frontend/src/api/client.ts:461-468` |
| `getForecast` legacy fallback | `GET /api/forecast?symbol=&horizon=` (older backends only) | NOT served by current code (no such route in `backend/api/forecast.py`) — fallback retained client-side only | `frontend/src/api/client.ts:469-475` |
| `getAnalytics(symbol)` path-style (primary) | `GET /api/analytics/{symbol}` | `backend/api/analytics_api.py:159-163` | `frontend/src/api/client.ts:513-517` |
| `getAnalytics` legacy fallback | `GET /api/analytics?symbol=` (older backends only) | NOT served by current code | `frontend/src/api/client.ts:519-525` |
| `runBacktest(symbol, horizons)` (primary) | `POST /api/backtest/run {symbol, horizons[, train_size, test_size, gap, n_bins, limit]}` | `backend/api/backtest.py:243-249` | `frontend/src/api/client.ts:577-581` |
| `runBacktest` legacy fallback | `POST /api/backtest {symbol, horizons}` (older backends only) | NOT served by current code | `frontend/src/api/client.ts:582-589` |
| `getAIPerformance()` (primary) | `GET /api/ai/providers/performance[?exchange&horizon]` | `backend/api/ai.py:228-236` | `frontend/src/api/client.ts:729-732` |
| `getAIPerformance` legacy fallback | `GET /api/ai/performance` (older backends only) | NOT served by current code | `frontend/src/api/client.ts:734-740` |
| `postAIInsight(symbol, profile)` | `POST /api/ai/insight {symbol, profile}` (no fallback; own 60s timeout) | `backend/api/ai.py:169-186` | `frontend/src/api/client.ts:664-673` |
| `testProviderHealth(provider)` (primary) | `POST /api/providers/health/test` (+ `?provider=` query echoed) | `backend/api/providers.py:26-34` | `frontend/src/api/client.ts:756-775` |
| `testProviderHealth` fallback | `POST /api/ai/test` (older backends only) | NOT served by current code (no such route in `backend/api/ai.py`) | `frontend/src/api/client.ts:776-782` |
| `getProvidersHealth()` | `GET /api/providers/health` | `backend/api/providers.py:13-23` | `frontend/src/api/client.ts:1119-1126` |
| `getScreener({market, minDirection, horizon, limit})` | `GET /api/screener?market=&min_direction=&horizon=&limit=` (no fallback) | `backend/api/screener.py:69-80` (single `GET ""` → `/api/screener`) | `frontend/src/api/client.ts:1373-1387` |
| quote | `GET /api/market_data/quote?symbol=&market=` | `backend/api/market_data.py:45-56` | `frontend/src/api/client.ts:258-272` |
| bars | `GET /api/market_data/bars?symbol=&timeframe=&limit=` | `backend/api/market_data.py:58-65` | — |
| securities quote/bars | `GET /api/securities/{instrument_id}/quote\|bars` | `backend/api/market_data.py:68-93` (`404 {"detail": "unknown instrument_id"}`) | — |
| alerts CRUD | `POST /api/alerts[/]`, `GET /api/alerts[/][?active_only=]`, `PATCH /api/alerts/{alert_id}`, `DELETE /api/alerts/{alert_id}` (204) | `backend/api/alerts.py:252-253,286-287,315-321,348-366` | — |
| provider keys | `POST /api/providers/keys`, `GET /api/providers/keys/status` | `backend/api/providers.py:121-157,160-182` | — |
| provider budgets | `POST /api/providers/budget`, `GET /api/providers/budget` | `backend/api/providers.py:185-216,219-238` | — |
| AI providers | `GET /api/ai/providers/performance`, `POST /api/ai/providers/health/test` | `backend/api/ai.py:228-249` | — |
| cron | `GET\|POST /api/cron/ingest`, `GET\|POST /api/cron/calibrate`, `GET\|POST /api/cron/evaluate` | `backend/api/cron.py:114-161,243-292,338-379` | — |
| FX | `GET /api/fx/pairs`, `GET /api/fx/rate?base=&quote=`, `POST /api/fx/convert`, `POST /api/fx/rank` | `backend/api/fx.py:121-132,135-160,163-188,191-257` | `frontend/src/api/client.ts:940-945,974-987,1083-1094` |
| health | `GET /health` (+ `/` root) | `backend/api/health.py:28-40`, `backend/api/main.py:94-96` | `frontend/src/api/client.ts:214-217` (`api.get('/health')`) |
| audit | `GET /api/audit/forecasts`, `GET /api/audit/ai_decisions` | `backend/api/audit.py` (see M3/M4 appendix) | `frontend/src/api/client.ts:1187-1189` |

Frontend timeouts: shared `axios` instance `timeout: 15000` (`frontend/src/api/client.ts:142-146`);
`AI_TIMEOUT_MS = 60000` for `postAIInsight` (`frontend/src/api/client.ts:664-673`, message in
`friendlyAIError` at `frontend/src/api/client.ts:676-682`); `SCREENER_TIMEOUT_MS = 60000`
for `getScreener` (`frontend/src/api/client.ts:1371-1386`). Serverless
`functions.api/index.py.maxDuration = 60` (`vercel.json:7-11`) — matches the 60s AI/screener budget.

### `/health` rewrite behavior

`vercel.json:22-34` rewrites: `/api/(.*) → /api` (serverless function `api/index.py`),
`/health → /api` (same function), SPA fallback otherwise. `api/index.py:20` imports
`backend.api.main:app`, which mounts `GET /health` (`backend/api/main.py:79`,
`backend/api/health.py:28-40` returning `{status, postgres, redis, version, providers}`).
So deployed `GET /health` hits the serverless function and returns the FastAPI health
payload; locally it hits FastAPI directly.

### Cron schedules + alerts-via-Actions

- Vercel crons (`vercel.json:12-21`): `GET /api/cron/ingest` at `0 1 * * *` (01:00 UTC),
  `GET /api/cron/calibrate` at `0 2 * * *` (02:00 UTC). Both also accept `?symbol=` and
  `POST {symbols: [...]}` manual runs (`backend/api/cron.py:114-161,243-292`).
- Alerts evaluation is NOT a third Vercel cron (Hobby slot cap — both slots taken, see
  `.github/workflows/alerts.yml:1-9`): GitHub Actions `Evaluate alerts` runs
  `*/15 * * * *` + `workflow_dispatch` (`.github/workflows/alerts.yml:12-17`) and curls
  `$APP_URL/api/cron/evaluate` with `Authorization: Bearer $CRON_SECRET`
  (`.github/workflows/alerts.yml:33-39`). `GET|POST /api/cron/evaluate` shares
  `evaluate_due_alerts` with the worker (`backend/api/cron.py:295-379`,
  `backend/api/alerts.py:438-580`).
- Cron auth: when `CRON_SECRET` is set, all six cron endpoints require
  `Authorization: Bearer <secret>` (constant-time compare) else `401 {"detail": "unauthorized"}`
  (`backend/api/cron.py:65-80`); when unset they are open (local dev).

### New surface 1: screener — `GET /api/screener` (`backend/api/screener.py:69-142`)

Request params: `market` (MIC in `{XNYS, XNAS, XSHG, XPAR, XAMS, XBRU}` or `ALL`/omitted;
`backend/api/screener.py:36,56-66`), `min_direction` (float `0.0–1.0`, default `0.5`),
`horizon` (`5|21|63`, default `21`), `limit` (`1–50`, default `20`).
Errors: bad `horizon` → `422 {"detail": "horizon must be one of [5, 21, 63], got ..."}`;
unknown `market` → `422 {"detail": "unknown market ...: expected one of [...] or ALL"}`.
Per-symbol failures degrade to `skipped: [{symbol, reason}]`, never a batch 500.

```json
{
  "results": [
    {
      "symbol": "AAPL", "company_name": "Apple Inc.", "exchange_mic": "XNAS",
      "currency": "USD", "price": 232.1, "change_pct": 0.42, "market_state": "open",
      "direction_probability": 0.61, "confidence": "moderate",
      "model_version": "ensemble-v1", "horizon": 21, "horizons": [21],
      "quality": {"metric": "piotroski", "quality_flag": "...", "reason": "..."},
      "provenance": {"source": "...", "as_of": "...", "delay_minutes": 15, "quality_grade": "B", "fallback_used": false, "missing_fields": []}
    }
  ],
  "count": 1, "universe_size": 42, "skipped": [], "horizon": 21,
  "disclosure": "Not investment advice. For informational purposes only."
}
```

(Field names copied from `backend/api/screener.py:108-123,135-142`; quality signal is
`piotroski_score({})` per `backend/api/screener.py:40-53`, never a fabricated score.)

### New surface 2: alerts — `/api/alerts*` (`backend/api/alerts.py:56,252-366`)

- `POST /api/alerts[/] {symbol, condition, threshold, horizon_days=21, target_ccy="USD"}`
  → `{alert, provenance, disclosure}`. `condition ∈ {price_above, price_below,
  direction_above, direction_below, change_pct_below}` (`backend/api/alerts.py:60-70`);
  `horizon_days ∈ {5,21,63}` for `direction_*` only; symbol must resolve via registry
  else `422 {"detail": "unknown symbol ..."}` (`backend/api/alerts.py:261-265`);
  non-finite `threshold` → `422 {"detail": "threshold must be a finite number"}`
  (`backend/api/alerts.py:176-198,225-246`); bad `horizon_days`/`target_ccy` →
  pydantic `422`; row shape `{alert_id, symbol, exchange_mic, condition, threshold,
  horizon_days, target_ccy, is_active, cooldown_hours=24, last_fired_at, created_at}`
  (`backend/api/alerts.py:108-121,266-275`).
- `GET /api/alerts[/][?active_only=false]` → `{alerts, count, provenance, disclosure}`
  (`backend/api/alerts.py:286-312`).
- `PATCH /api/alerts/{alert_id} {is_active?, threshold?, cooldown_hours?≥0}` →
  `{alert, provenance, disclosure}`; empty subset → `422 {"detail": "no updatable fields:
  expected subset of {is_active, threshold, cooldown_hours}"}` (`backend/api/alerts.py:315-345`);
  unknown id → `404 {"detail": "unknown alert ..."}` (`backend/api/alerts.py:134-155`).
- `DELETE /api/alerts/{alert_id}` → `204` empty (events cascade;
  `backend/api/alerts.py:348-366`).
- Evaluation `POST|GET /api/cron/evaluate` → `{checked, fired: [{alert_id, symbol,
  observed}], errors, provenance, disclosure}` (`backend/api/alerts.py:438-580`).

### New surface 3: provider keys / budgets — `/api/providers/keys*|budget*` (`backend/api/providers.py:37-238`)

Allowed `provider ∈ {gemini, openai, anthropic, xai}` (`backend/api/providers.py:39-40`);
unknown → `422 {"detail": "unknown provider; expected one of [...]"}` (`backend/api/providers.py:43-47`).

- `POST /api/providers/keys {provider, model?, api_key}` → `{ok: true, provider, model,
  configured: true}` (encrypted at rest via `put_db_secret`; `backend/api/providers.py:121-157`).
  `model` must be a string ≤ 64 chars else `422 {"detail": "model must be a string <= 64 characters"}`
  (`backend/api/providers.py:50-57`); `api_key` non-empty ≤ 2000 chars else
  `422 {"detail": "api_key must be a non-empty string" | "api_key must be <= 2000 characters"}`
  (`backend/api/providers.py:60-65`); non-dict body → `422 {"detail": "body must be {provider, model?, api_key}"}`.
- `GET /api/providers/keys/status` → `{providers: [{provider, model, configured, updated_at}]}`
  config flags only, never key material (`backend/api/providers.py:160-182`);
  `configured` = secret-store OR env OR DB (`backend/api/providers.py:95-118`).
- `POST /api/providers/budget {provider, monthly_usd}` → `{ok: true, provider, monthly_usd}`
  (`backend/api/providers.py:185-216`); `monthly_usd` finite ≥ 0 else
  `422 {"detail": "monthly_usd must be a finite number >= 0"}` (`backend/api/providers.py:68-79`);
  non-dict body → `422 {"detail": "body must be {provider, monthly_usd}"}`.
- `GET /api/providers/budget` → `{budgets: {provider: monthly_usd}}`
  (`backend/api/providers.py:219-238`).
- Dashboard: `GET /api/providers/health` → `{providers: [...]}` with zero-row stub
  (`backend/api/providers.py:13-23`); `POST /api/providers/health/test` reference-quote
  probe (`backend/api/providers.py:26-34`).

---

## M9 appendix (PROPOSAL — market index / ASPI surfaces; frontend-team draft, NOT implemented)

Status: **non-normative proposal** from Frontend Agent 4+5. No backend route
changes here; existing `/api/markets/*`, `/api/market_data/*`, and
`/api/screener` routes are untouched. The frontend ships a frontend-only
implementation (`frontend/src/api/aspi.js` + `frontend/src/components/AspiChart.jsx`)
on top of existing endpoints; this appendix asks the backend team for native
surfaces so proxies and client-side composites can be retired.

### Why

- There is no index time series anywhere (`backend/api/markets.py` serves
  breadth aggregates; `backend/api/screener.py` serves forecast-ranked rows).
- Backend `validate_symbol` (`backend/security/validation.py:17`) rejects `^`,
  so canonical benchmark symbols (`^NYA`, `^IXIC`, `^FCHI`, `^AEX`, `^BFX`)
  422 today. The frontend falls back to ETF proxies (labelled PROXY in the UI).
- The Top-20 composite is equal-weighted client-side because no endpoint
  exposes market-cap weights.

### Proposal 1: allow caret index symbols (smallest change)

Relax `SYMBOL_RE` to accept a single leading `^` (e.g.
`r"^\^?[A-Za-z0-9][A-Za-z0-9.\-:]{0,31}$"`), or exempt a registry-backed
index allowlist. Frontend primaries per MIC:

| MIC | Canonical index | Frontend proxy today |
|---|---|---|
| XNYS | `^NYA` (NYSE Composite) | `SPY` |
| XNAS | `^IXIC` (Nasdaq Composite) | `QQQ` |
| XSHG | `000001.SS` (SSE Composite, already valid) | — |
| XPAR | `^FCHI` (CAC 40) | `CAC.PA`, `EWQ` |
| XAMS | `^AEX` (AEX) | `IAEX.AS`, `EWN` |
| XBRU | `^BFX` (BEL 20) | `EWK` |
| XCOL (future) | CSE ASPI vendor symbol TBD | — |

### Proposal 2: `GET /api/markets/{mic}/index` (preferred long-term)

```http
GET /api/markets/XNAS/index?timeframe=1d&limit=90&constituents=top20
```

- Query: `timeframe ∈ {1d, 1wk, 1mo}` (mirror `validate_timeframe`),
  `limit` 1–250 (mirror `/api/market_data/bars`),
  `constituents ∈ {none, top20}` (default `none`).
- `{mic}` validates like `/{mic}/liquidity` (unknown MIC → `422`).
- `constituents=top20` returns the turnover-sorted Top-20 with per-row
  `weight` (index weight when known, else `null` — never fabricated) so the
  frontend can render an index-weighted line instead of equal-weighted.

```json
{
  "mic": "XNAS",
  "index_symbol": "^IXIC",
  "index_label": "Nasdaq Composite",
  "timeframe": "1d",
  "bars": [{ "ts": "2026-01-15", "close": 21500.12 }],
  "constituents": [
    {
      "symbol": "AAPL", "company_name": "Apple Inc.", "currency": "USD",
      "price": 232.1, "change_pct": 0.42, "turnover": 1.2e10,
      "weight": null
    }
  ],
  "weighting": "equal (market-cap weights unavailable)",
  "turnover_note": "Turnover sums native price*volume per symbol with no FX conversion (mixed currencies).",
  "provenance": {"source": "...", "as_of": "...", "delay_minutes": 15, "quality_grade": "B", "fallback_used": false, "missing_fields": ["market-cap-weights"]},
  "disclosure": "Not investment advice. For informational purposes only."
}
```

- Partial failure degrades per-symbol to `skipped: [{symbol, reason}]`
  (the `markets.py`/`screener.py` pattern) — never a batch 500.
- `weighting` is `"index"` only when every returned constituent carries a
  real weight; otherwise `"equal (market-cap weights unavailable)"` and
  `"market-cap-weights"` appears in `missing_fields`.

### Proposal 3: XCOL venue slot (Sri Lanka CSE ASPI, extensible)

Frontend carries a disabled `XCOL` benchmark entry (LKR, Asia/Colombo) that
renders as a "coming soon" teaser only. To light it up (no frontend code
change needed beyond `enabled: true`):

```yaml
# config/markets.yaml — append (MIC ^[A-Z]{4}$, real ZoneInfo timezone):
- mic: XCOL
  name: Colombo Stock Exchange
  provider_suffix: ""        # confirm vendor symbol style first
  timezone: Asia/Colombo
  currency: LKR
  country: LK
  delay_minutes: 15
  enabled: true
  ingest: true
```

Open items for the backend team: confirm the vendor index symbol for the
CSE All-Share Price Index, the quote suffix convention for CSE listings,
and the trading calendar (weekend days, holidays) for `market_state`.

---

## Overlay appendix (chart indicators — normative, Backend Agent 5 repaired)

Engine: `backend/analytics/technical/overlays.py`
(`SUPPORTED_INDICATORS`, `parse_indicators`, `compute_indicators`).
Pure/vectorized/deterministic; no network, randomness, or wall-clock reads.
Formulas mirror `backend/analytics/technical/indicators.py`
(SMA/EMA rolling/ewm, RSI Wilder, MACD 12/26/9, BB 20/2 population std,
ATR Wilder, VWAP cumulative typical-price, volume SMA20).

### Supported names + aliases

Canonical `SUPPORTED_INDICATORS` (upper-case on the wire):

```json
["SMA20", "SMA50", "SMA200", "EMA12", "EMA26", "RSI14", "MACD", "BB20", "VWAP", "ATR14", "VOLUME_SMA20"]
```

Parsing is case-insensitive, ignores spaces/`_`/`-`, dedupes (first wins):
`sma_20`/`sma-20`/`sma 20` → `SMA20`; `rsi` → `RSI14`;
`bb`/`bollinger`/`bollinger20` → `BB20`; `atr` → `ATR14`;
`vol`/`volume`/`vol_sma20` → `VOLUME_SMA20`.

### `GET /api/analytics/{symbol}?indicators=...`

- Query `indicators`: comma-separated canonical/alias names
  (e.g. `?indicators=SMA20,EMA12,RSI14,MACD,BB20,VWAP,ATR14`).
  Omitted/blank → backward compat: **no** `indicators` key (snapshot-only,
  `technical` latest-values unchanged).
- Unknown token → `422 {"detail": "unknown indicator(s): FOO; expected one of
  ['SMA20', 'SMA50', 'SMA200', 'EMA12', 'EMA26', 'RSI14', 'MACD', 'BB20',
  'VWAP', 'ATR14', 'VOLUME_SMA20']"}` exactly
  (`backend/analytics/technical/overlays.py:parse_indicators`,
  surfaced by `backend/api/analytics_api.py` + `backend/api/market_data.py`).
- Success adds `indicators: {requested, bars, max_points, series,
  provenance}` plus flat duplicates of each series entry at the same level
  (so the existing frontend `normalizeIndicators()` passthrough in
  `frontend/src/api/client.js:1356-1417` keeps working; it ignores the
  metadata keys). `max_points` is `1000` (last-N truncation after full-history
  computation, so SMA values stay stable).

```json
{
  "symbol": "AAPL",
  "provenance": {"source": "...", "as_of": "...", "delay_minutes": 15, "quality_grade": "B", "fallback_used": false, "missing_fields": []},
  "indicators": {
    "requested": ["SMA20", "BB20", "MACD"],
    "bars": 120,
    "max_points": 1000,
    "series": {
      "SMA20": [{"time": "2026-01-15", "value": 232.1}],
      "BB20": {"upper": [...], "middle": [...], "lower": [...]},
      "MACD": {"macd": [...], "signal": [...], "histogram": [...]}
    },
    "SMA20": [{"time": "2026-01-15", "value": 232.1}],
    "BB20": {"upper": [...], "middle": [...], "lower": [...]},
    "MACD": {"macd": [...], "signal": [...], "histogram": [...]},
    "provenance": {"source": "...", "as_of": "...", "delay_minutes": 15, "quality_grade": "B", "fallback_used": false, "missing_fields": []}
  }
}
```

### `GET /api/market_data/indicators?symbol=&indicators=&timeframe=&limit=`

Distinct path (never collides with `/quote` or `/bars`):
`backend/api/market_data.py:market_indicators`.

- `symbol` required; `indicators` optional (same parse + 422 as above);
  `timeframe ∈ {1d, 1wk, 1mo}` (default `1d`);
  `limit` 1–250 (default `120`, mirrors `/bars`).
- Response: `{symbol, timeframe, requested, bars, max_points, series,
  indicators: {requested, bars, max_points, series, provenance, +flat},
  provenance}`.

### Series semantics (both endpoints)

- Point: `{time: "YYYY-MM-DD", value: number|null}` (`IndicatorPointSchema`,
  `frontend/src/api/client.js:1268-1271`). Times are bar dates ascending,
  deduped (last wins).
- Single-line overlays (`SMA20/SMA50/SMA200/EMA12/EMA26/RSI14/VWAP/ATR14/
  VOLUME_SMA20`): array of points.
- `BB20`: `{upper, middle, lower}` arrays → frontend `BB_UPPER/BB_MIDDLE/
  BB_LOWER`. `MACD`: `{macd, signal, histogram}` arrays → frontend
  `MACD_LINE/MACD_SIGNAL/MACD_HIST`.
- Warmup is `null`, never `0` (frontend drops nulls as line gaps).
- Insufficient history or missing fields → per-indicator
  `{"status": "unavailable", "reason": "..."}` (never fabricated, never a
  batch 500; frontend renders no line). Minimum bars: SMA20 20, SMA50 50,
  SMA200 200, EMA12 12, EMA26 26, RSI14 15, MACD 35, BB20 20, VWAP 1,
  ATR14 15, VOLUME_SMA20 20.
- Frontend alignment (read-only): `PriceChart.jsx:42-43` documents
  `GET /api/analytics/{symbol}?indicators=...`; `SUPPORTED_INDICATORS`
  (10 chart names, `VOLUME_SMA20` backend-only until the picker adds it)
  drives `requestedIndicators` + legend in `SecurityBrief.jsx:65-70`.
