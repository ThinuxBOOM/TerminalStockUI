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
- Returns **strict-schema JSON** bounded opinion: `direction, probability (0–1),
  time_horizon_days (5|21|63 only), catalysts[], risks[], evidence_ids[], limitations[]`.
- Rejects claims without `evidence_ids`; rejects out-of-range probabilities/horizons.
- `ai_weight ≤ 0.20`, server-enforced. Disabling AI leaves `/forecast` intact.
- API keys never accepted from, or returned to, the client.

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

- Query: `horizon_days` ∈ `{5, 21, 63}` only; any other value → `400 INVALID_HORIZON`.
- Response: same shape as the `GET .../forecast` example above, plus:
  - `model_version`, `feature_version`, `data_version` (all required, non-empty).
  - `created_at` (row timestamp), `target_date` (as_of + horizon).
  - `ai_provider / ai_model / ai_weight` (`ai_weight` ∈ `[0, 0.20]`, `0` when AI disabled).
  - `disclosure: "Not investment advice. For informational purposes only."`
  - `provenance` envelope (required).
- Every call persists a versioned row to `forecasts`
  (`UNIQUE (instrument_id, horizon_days, target_date, model_version,
  feature_version, data_version)`) and emits `forecast.created` to the audit log.
- Grade D data → `409 FORECAST_BLOCKED` with `{"value": null, "status": "unavailable"}` semantics; never fabricate.

### `GET /api/securities/{instrument_id}/analytics` (M2, referenced by M3/M4)

- Sections: `technical, fundamentals, quality, valuation, events`.
- Every metric: `{value, formula, inputs, provenance}`; missing → `{value: null, status: "unavailable", reason}`.

### `GET /api/securities/{instrument_id}/backtest?horizon_days=21` (M3, lightweight)

- Walk-forward only, time-ordered splits, corporate-action-adjusted prices.
- Response: `{instrument_id, horizon_days, folds, brier_score, calibration_error, reliability_table, model_version, feature_version, data_version, provenance}`.
- Failure folds are reported, never hidden (M3 acceptance: dashboard shows failures too).

### `POST /api/securities/{instrument_id}/ai-insight` (M4/M5)

- Explicit-request only. Body: `{profile: quick_insight|forecast_assist|deep_research|report, provider: gemini|openai|anthropic|xai, model: "..."}`.
- Returns strict-schema bounded opinion: `direction (bullish|bearish|neutral), probability (0–1), time_horizon_days (5|21|63 only), catalysts[], risks[], evidence_ids[] (non-empty), limitations[]`.
- Server-enforced: reject claims without `evidence_ids`; reject out-of-range probabilities/horizons; `ai_weight ≤ 0.20`.
- Keys: never accepted from, or returned to, the client. Request/response payloads are redacted in logs and audit rows.

### `POST /api/securities/{instrument_id}/ai-forecast-opinion` (M5, bounded)

- Same validation as `ai-insight` with `forecast_assist` profile semantics: may raise/lower/leave unchanged the displayed confidence but never overrides the deterministic forecast. Disabling AI leaves `/forecast` intact.

### `GET /api/ai/providers/performance?exchange=XNAS&horizon_days=21` (M5)

- Historical provider/model scoreboard by exchange and horizon: `{provider, model, exchange, horizon_days, n_opinions, brier_score, calibration_error, hit_rate}`.
- Used to justify the fixed 20% cap and any future learned weighting (only after sufficient out-of-sample evidence).

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

### Error codes (all endpoints)

| `error.code` | HTTP | Meaning |
|---|---|---|
| `INVALID_HORIZON` | 400 | `horizon_days` not in `{5, 21, 63}` |
| `UNKNOWN_INSTRUMENT` | 404 | `instrument_id` not in registry/DB |
| `FORECAST_BLOCKED` | 409 | Grade-D data; forecast unavailable, reason in `message` |
| `AI_VALIDATION_FAILED` | 422 | Provider returned malformed opinion (missing `evidence_ids`, bad probability/horizon) |
| `AI_DISABLED` | 409 | AI requested but no provider configured; deterministic `/forecast` still works |
| `PROVIDER_UNAVAILABLE` | 502 | Upstream failed; cached `fallback_used: true` payload when available |
| `RATE_LIMITED` | 429 | Provider/token-bucket budget exhausted; `retryable: true` |

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

Gate refusal:

```json
{"error": {"code": "FX_PROVENANCE_MISSING", "message": "FX feed stale/missing; ranking refused", "retryable": true, "provenance": {...}}}
```

### Error codes (M7 addition)

| `error.code` | HTTP | Meaning |
|---|---|---|
| `FX_PROVENANCE_MISSING` | 409 | FX provenance stale/missing; `/api/fx/rank` (and convert when applicable) refused. Client shows the gate message and native-currency quotes only. |
