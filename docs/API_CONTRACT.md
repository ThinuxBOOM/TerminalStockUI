# API contract

All routes are served under the app's origin (Caddy proxies `/api/*` and
`/health`). Full request and response schemas: run the backend with
`APP_ENV=development` and open `/docs` (disabled in production).

## Conventions

- **Auth.** Every `/api/*` route requires `Authorization: Bearer <access
  token>` except `/api/auth/*` and `/api/cron/*`. Missing, expired or revoked
  tokens get `401`; admin-only routes return `403` to other users.
- **Cron.** `/api/cron/*` requires `Authorization: Bearer <CRON_SECRET>`.
- **Timestamps** are ISO 8601 UTC. Money carries an explicit `currency`.
- **Fail closed.** Market data is live or an error (`502`), never a stale or
  synthetic `200`. Missing fields are listed, never zero-filled.
- **Disclosure.** Forecast and AI responses carry `disclosure`; clients must
  show it. Forecasts carry `validation_status: "experimental"` and
  `measured_skill` (below).

### Provenance envelope

Every data-bearing response includes:

```json
{
  "source": "yfinance",
  "as_of": "2026-09-30T16:52:11Z",
  "delay_minutes": 15,
  "quality_grade": "B",
  "fallback_used": false,
  "missing_fields": [],
  "granularity": "1d"
}
```

| Field | Meaning |
|---|---|
| `source` | provider that served the data |
| `as_of` | when it was fetched (quotes: see also `price_time`, when the price was set) |
| `delay_minutes` | known feed delay |
| `quality_grade` | A to D (`docs/DATA_QUALITY.md`) |
| `fallback_used` | always `false` on success (fallback data is refused) |
| `missing_fields` | fields that are honestly unavailable |
| `granularity` | `"1d"` for data built from daily bars: current when it covers the last completed session, so minute-level age does not apply. Absent otherwise. |

Daily bars carry `ts` (the session's local midnight, as a UTC instant) and
`date` (the exchange-local session day). Use `date` for calendar logic: the
UTC date of `ts` is a day early for Shanghai and Euronext.

### Measured skill

`measured_skill` on a forecast (one entry of the `measured-skill` table) is
the pooled walk-forward record of the up probability for that horizon and
calibration, from `scripts/evaluate_forecasts.py`. `null` when the running
model version has not been evaluated.

```json
{
  "horizon_days": 7,
  "calibration": "shrinkage",
  "skill": -0.0311,
  "ci95": [-0.0382, -0.0239],
  "verdict": "worse",
  "symbols": 100,
  "points": 31450,
  "as_of": "2026-10-01",
  "summary": "In walk-forward tests on 100 stocks, 7-day direction probabilities scored worse than the historical base rate (skill -0.031, 95% CI -0.038 to -0.024)."
}
```

`skill` is the Brier skill score against the base rate (> 0 beats it).
`verdict` is `worse` or `better` only when the 95% interval excludes zero,
otherwise `indistinguishable`.

### Errors

FastAPI shape: `{"detail": "..."}` (or an object for structured errors).

| Status | When |
|---|---|
| 401 | not signed in, token expired/revoked, wrong `CRON_SECRET` |
| 403 | admin-only route; registration closed |
| 404 | unknown instrument, alert or job (another user's alert/job is also 404) |
| 409 | email already registered |
| 413 | request body over `MAX_REQUEST_BYTES` |
| 422 | invalid input (symbol shape, horizon not in 1/7/14/21, unknown AI profile, extra fields on cron retention) |
| 423 | AI provider not configured; FX rank refused (`code: FX_PROVENANCE_MISSING`, rates stale or missing) |
| 429 | rate limit (`Retry-After` header) or daily AI limit |
| 502 | upstream provider returned no live data |

## Endpoints

### Auth (`/api/auth`, public)

| Method | Path | Notes |
|---|---|---|
| GET | `/config` | `{registration_open, min_password_length}` |
| POST | `/register` | `{email, password}` → `201` user + `access_token`; sets refresh cookie. `403` when registration is closed |
| POST | `/login` | `{email, password}` → user + `access_token` (15 min); sets `refresh_token` cookie (7 d, HttpOnly, SameSite=Strict, path `/api/auth`) |
| POST | `/refresh` | refresh cookie → new access token, rotated cookie |
| POST | `/logout` | bearer required; revokes all of the user's tokens → `204` |
| GET | `/me` | bearer required; `{id, email, is_admin}` |

### Instruments and market data

| Method | Path | Query |
|---|---|---|
| GET | `/api/instruments/search` | `q, market (MIC), limit, offset` |
| GET | `/api/instruments/resolve` | `symbol, market` |
| GET | `/api/instruments/{instrument_id}` | |
| GET | `/api/market_data/quote` | `symbol, market`; includes `price_time`, `market_state` (open/closed/lunch/delayed/stale) |
| GET | `/api/market_data/bars` | `symbol, timeframe (1d/1wk/1mo), limit ≤ 1000` |
| GET | `/api/market_data/chart` | as bars, with the live quote stitched into the last bar (`stitched`, `forming`) |
| GET | `/api/market_data/indicators` | `symbol, indicators (comma list), timeframe, limit` |
| GET | `/api/securities/{instrument_id}/quote` | |
| GET | `/api/securities/{instrument_id}/bars` | `timeframe, limit` |
| GET | `/api/markets` | supported venues |
| GET | `/api/markets/overview` | `target_ccy` |
| GET | `/api/markets/{mic}/index` | benchmark series, `timeframe` |
| GET | `/api/markets/{mic}/liquidity` | `target_ccy, limit, sort` |
| GET | `/api/markets/{mic}/liquidity/history` | `window (1D/5D/1M/3M/6M/1Y)` |
| GET | `/api/fx/pairs`, `/api/fx/rate` | `base, quote` |
| POST | `/api/fx/convert` | `{amount, from, to}` |
| POST | `/api/fx/rank` | `{symbols, target_ccy}`; `423` without fresh FX |
| GET | `/api/news`, `/api/news/symbol/{symbol}` | needs Alpaca keys (`423` otherwise) |
| GET | `/api/sentiment/premarket` | best-effort |

### Analytics, forecasts, backtests

| Method | Path | Query / body |
|---|---|---|
| GET | `/api/analytics/{symbol}` | `indicators` |
| GET | `/api/forecast/{symbol}` | `horizon ∈ {1, 7, 14, 21}`; includes `measured_skill` |
| GET | `/api/forecast/measured-skill` | pooled walk-forward skill per horizon, `shrinkage` and `isotonic` |
| GET | `/api/forecast/{symbol}/calibration/history` | `horizon, limit` |
| POST | `/api/backtest/run` | `{symbol, horizons}` (walk-forward, leakage-guarded) |
| GET | `/api/backtest/{symbol}` | `include_reliability` |
| GET | `/api/screener` | `market (MIC or SP500), min_direction, horizon, limit ≤ 50, offset` |
| GET | `/api/signals/top` | `horizon, per_market` |

### AI (explicit request only)

| Method | Path | Notes |
|---|---|---|
| POST | `/api/ai/insight` | `{symbol, profile}`; counts toward the daily AI limit |
| POST | `/api/ai/forecast_opinion` | `{symbol, horizon, profile, quant_prob, ai_weight ≤ 0.20, ai_enabled}` |
| POST | `/api/ai/deep_research_job` | `202 {job_id}`; poll the next route |
| GET | `/api/ai/jobs/{job_id}` | creator only |
| GET | `/api/ai/providers/performance` | `exchange, horizon` |
| POST | `/api/ai/providers/health/test` | configuration only, never key material |

### Alerts (per user)

| Method | Path | Notes |
|---|---|---|
| POST | `/api/alerts/` | `{symbol, condition, threshold, horizon_days?, target_ccy?}` |
| GET | `/api/alerts/` | `active_only, limit ≤ 500, offset` |
| PATCH | `/api/alerts/{alert_id}` | subset of `{is_active, threshold, cooldown_hours}` |
| DELETE | `/api/alerts/{alert_id}` | `204` |

### Providers and audit

| Method | Path | Access |
|---|---|---|
| GET | `/api/providers/health` | any user; read-only |
| POST | `/api/providers/health/test` | admin; `provider` query |
| GET/POST | `/api/providers/keys/status`, `/api/providers/keys` | admin; keys are write-only |
| GET/POST | `/api/providers/budget` | admin |
| GET | `/api/audit/forecasts` | any user; `symbol, instrument_id, horizon_days, limit, offset` |
| GET | `/api/audit/ai_decisions` | any user; `provider, limit, offset` |

### Scheduler (`/api/cron`, `CRON_SECRET`)

`ingest` (`symbol`, or `universe=sp500&shard=N&shards=M`), `calibrate`,
`evaluate`, `snapshot`, `score`, `health`: GET or POST. `retention`: GET is a
dry run; POST `{"apply": true}` purges (windows come from server config
only). Batches report per-symbol `errors` instead of failing whole.

### Health

`GET /health` (public, in-memory only): `{status, postgres, redis, version,
providers}`. `?deep=1` adds a live database and cache round trip.
