# API contract

All routes are served under the app's origin (Caddy proxies `/api/*` and
`/health`). Full request and response schemas: run the backend with
`APP_ENV=development` and open `/docs` (disabled in production).

## Conventions

- **Auth.** Every `/api/*` route requires `Authorization: Bearer <access
  token>` except `/api/auth/*`, `/api/cron/*` and `/api/public/*`. Missing, expired or revoked
  tokens get `401`; admin-only routes return `403` to other users.
- **Cron.** `/api/cron/*` requires `Authorization: Bearer <CRON_SECRET>`.
- **Timestamps** are ISO 8601 UTC. Money carries an explicit `currency`.
- **Fail closed.** Market data is live or an error (`502`), never a stale or
  synthetic `200`. Missing fields are listed, never zero-filled.
- **Disclosure.** Forecast and AI responses carry `disclosure`; clients must
  show it. Forecasts carry `validation_status: "measured"` and their
  walk-forward record in `measured` (below).

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

### Forecast payload (engine v4)

`GET /api/forecast/{symbol}?horizon=21` (abridged):

```json
{
  "symbol": "NVDA", "exchange_mic": "XNAS", "horizon_days": 21,
  "as_of": "2026-10-01", "target_date": "2026-10-30",
  "expected_return_range": {"low": -0.099, "mid": 0.014, "high": 0.124, "coverage": "80% (volatility model)"},
  "quantiles": {"0.05": -0.14, "0.10": -0.099, "0.25": -0.04, "0.50": 0.014, "0.75": 0.06, "0.90": 0.124, "0.95": 0.16},
  "price_quantiles": {"0.10": 207.98, "0.50": 233.9, "0.90": 259.57},
  "target_price": {"last_close": 230.86, "low": 207.98, "mid": 233.9, "high": 259.57},
  "volatility_forecast_annual": 0.33, "volatility_regime": "low",
  "drawdown_probability": 0.16, "drawdown_detail": {"threshold": 0.10, "horizon_days": 21},
  "outperform_probability": 0.498, "outperform_rank": 0.45, "relative_available": true,
  "signal_strength": "weak",
  "drivers": {"for": [{"feature": "dist_sma200", "label": "distance from the 200-day average", "contribution": 0.031, "percentile": 0.85}], "against": []},
  "direction_probability": 0.567, "direction_probability_raw": 0.571, "base_rate": 0.568,
  "confidence": "low",
  "measured": {"range_coverage_80": 0.793, "drop_risk_skill": 0.05, "out_ic": 0.025, "out_ic_t": 1.25, "out_decile_spread": 0.0076, "up_skill": -0.001, "up_skill_ci95": [-0.003, 0.001]},
  "summary": "Over the next 21 trading days, NVDA has typically moved between -9.9% and +12.4% (80% range). ...",
  "why": ["..."], "risks": ["..."], "limitations": ["..."],
  "model_version": "v4-20261001-1507", "feature_version": "v4-features-1", "data_version": "...",
  "cross_section_as_of": "2026-10-01",
  "validation_status": "measured", "disclosure": "...", "provenance": {"...": "..."}
}
```

- Returns are simple returns over the horizon (`-0.099` = -9.9%).
- `outperform_*` are `null` and `relative_available` is `false` outside US
  listings (the ranking model is trained on the S&P 500).
- `outperform_rank` is the stock's percentile among the universe that day
  (1 = best). `signal_strength`: `strong` (top/bottom 10%), `moderate`
  (top/bottom 25%), `weak`.
- `confidence` is `moderate` only for a strong ranking signal at a horizon
  whose ranking edge is statistically significant, otherwise `low`.
- `measured` is the walk-forward record for this horizon from the active
  model bundle.
- `422` when fewer than 253 daily bars are stored for the symbol.

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

### Analytics, forecasts, screening, risk

| Method | Path | Query / body |
|---|---|---|
| GET | `/api/analytics/{symbol}` | `indicators` |
| GET | `/api/forecast/{symbol}` | `horizon ∈ {1, 7, 14, 21}` (default 21) |
| GET | `/api/forecast/{symbol}/all` | every horizon: `{symbol, horizons: {"1": ..., "21": ...}}` |
| GET | `/api/forecast/model` | active model: version, training date, data range, walk-forward report per horizon |
| GET | `/api/forecast/model/{symbol}` | that stock's walk-forward record (`in_universe`, range coverage, ranking hit rate per horizon) |
| GET | `/api/screener` | `market (US/SP500, ALL or a MIC), horizon, sort (out_rank, p_up, drawdown, volatility, range_width, change, symbol), order, min_rank, max_drawdown, regime, sector, q, symbols (comma list), limit ≤ 200, offset`. Rows come from the daily scores; `pending: true` while the first scoring run is in progress |
| GET | `/api/signals/top` | `horizon, n ≤ 25`: `top` and `bottom` of the S&P 500 ranking, never overlapping |
| GET | `/api/risk/{symbol}` | `lookback ≤ 1000` sessions: volatility, drawdowns, VaR/CVaR (1/5/21 days), beta and correlation vs. the venue benchmark, Sharpe/Sortino, liquidity |
| POST | `/api/risk/portfolio` | `{holdings: [{symbol, weight}], lookback}` (1-25 holdings, weights in any positive units): volatility, VaR/CVaR, max drawdown, beta, diversification ratio, risk contributions, correlation matrix, worst days; `unavailable` lists holdings without history |

### Public (no account)

| Method | Path | Notes |
|---|---|---|
| GET | `/api/public/model` | aggregate walk-forward accuracy of the active model (no symbols, prices or user data); used by the landing page |

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

`ingest` (`symbol`, or `universe=sp500&shard=N&shards=M`), `evaluate`,
`snapshot`, `score`, `health`: GET or POST. `predict` (daily scoring) and
`train` (weekly retraining) start a child process and return
`{job, started, pid}`; POST `{"wait": true}` runs in-process instead. `retention`: GET is a
dry run; POST `{"apply": true}` purges (windows come from server config
only). Batches report per-symbol `errors` instead of failing whole.

### Health

`GET /health` (public, in-memory only): `{status, postgres, redis, version,
providers}`. `?deep=1` adds a live database and cache round trip.
