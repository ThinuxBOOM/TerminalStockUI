# OneMarket Analyzer — User Guide (v1)

Spec: `../markdown.md` §§1, 5 (M8). Deterministic analytics and forecasting are the
source of truth; AI adds structured event analysis and bounded forecast opinions only.

> **Disclosure:** forecasts are measurable probabilities, **not investment advice**.
> Every forecast view renders the disclosure string returned by the API:
> `Not investment advice. For informational purposes only.`

All money shows source, timestamp, delay/freshness, and quality grade
(`ProvenanceBadge` + `FreshnessBadge`). Missing data renders `unavailable` with a
reason — never zero-filled. Identity is always `(exchange_mic, symbol)`; the UI
displays the provider (Yahoo-style) form: `AAPL`, `600519.SS`, `MC.PA`.

## 0. Homepage (start here — no trading knowledge needed)

The homepage (`/`) is the beginner entry point: a 30-second hero ("Type any
company — we show the price, whether models lean up or down, and why in plain
words"), the market open/closed strip, then **What should I look at today?**
(`GET /api/signals/top?horizon=1|7|14|21` — Top-5 research-further + Top-5
be-careful per market, 80% ensemble math + 20% Alpaca news mood, never orders),
**My List** (watchlist, separate from ideas), **Market news** (Alpaca News with
mood badges), and collapsible deep-dives: market indexes (ASPI & friends),
market activity/liquidity, latest research, and data health (green = working).

## 1. Search → Security Brief

1. Open **Search**, type a ticker, name, or provider symbol, optionally narrow with the
   market filter (`All / NYSE / NASDAQ / SSE / Euronext Paris / Euronext Amsterdam /
   Euronext Brussels` → `?market=XNYS|XNAS|XSHG|XPAR|XAMS|XBRU`; All omits the param).
2. Examples:
   - `AAPL` (market All or NASDAQ) → `AAPL · Apple Inc. · XNAS · USD$`.
   - `Moutai`, `600519`, or `600519.SS` (market SSE) → `600519.SS · Kweichow Moutai
     Co., Ltd. · XSHG · CNY¥`.
   - `LVMH`, `MC`, or `MC.PA` (market Euronext Paris) → `MC.PA · LVMH Moet Hennessy
     Louis Vuitton SE · XPAR · EUR€`. Likewise `ASML`/`ASML.AS` (XAMS),
     `UCB`/`UCB.BR` (XBRU).
3. Multiple hits render a ranked `Ambiguous — N candidates` list. The UI never guesses;
   pick the exact provider symbol or narrow with the market filter.
4. Click **BRIEF →** for the Security Brief (`/security/:symbol`): native-currency price
   (`CurrencyValue` via `Intl.NumberFormat`: `USD$` / `CNY¥` / `EUR€`), `MarketStateBadge`
   (`OPEN/CLOSED/LUNCH/DELAYED/STALE`; `lunch` = XSHG 11:30–13:00 Asia/Shanghai only),
   price chart, events timeline, analytics snapshot, and the deterministic forecast card
   (probability, confidence, data quality, bull/bear drivers) — each with its
   `ProvenanceBadge`. All 500 S&P 500 tickers resolve (seeded index universe);
   any other ticker resolves on demand (bars+quote fetched live, then cached —
   a first cold view may take one auto-retry).

Backend equivalents: `GET /api/instruments/search?q=Moutai&market=XSHG`,
`GET /api/market_data/quote?symbol=600519.SS&market=XSHG`. Full shapes:
`docs/API_CONTRACT.md` (+ M6/M7 appendices), `docs/SSE_NOTES.md`, `docs/EURONEXT_NOTES.md`.

## 2. Forecast details (deterministic core)

Open **FORECAST DETAILS →** (`/forecast/:symbol`). Horizons are trading days `1 / 7 / 14 / 21`
only — any other value is rejected. The page shows direction probability, expected-return
interval (low/mid/high), volatility regime, drawdown probability, model/feature/data
versions, evidence IDs, calibration history (reliability table), limitations, and the
disclosure string.

Backend (same routes the UI now calls path-first — `getForecast` at
`frontend/src/api/client.ts:461-476`, `getAnalytics` at `frontend/src/api/client.ts:513-526`):

```powershell
# deterministic ensemble forecast (symbol is a path segment, horizon is a query param)
curl "http://localhost:8000/api/forecast/AAPL?horizon=21"
curl "http://localhost:8000/api/forecast/600519.SS?horizon=21"
curl "http://localhost:8000/api/forecast/MC.PA?horizon=14"
# analytics snapshot + lightweight walk-forward backtest
curl "http://localhost:8000/api/analytics/AAPL"
curl -X POST "http://localhost:8000/api/backtest/run" -H "Content-Type: application/json" `
  -d '{"symbol":"AAPL","horizons":[21]}'
```

Bad horizons are rejected with `422 {"detail": "horizon must be one of [1, 7, 14, 21], got ..."}`
(`backend/api/forecast.py:170-174`).

Grade-D inputs block forecasts instead of emitting uncalibrated numbers. Every forecast
row stores `model_version + feature_version + data_version + timestamp`; history is at
`GET /api/audit/forecasts?symbol=AAPL`.

## 3. AI opinion (explicit request only, capped 20%)

AI never runs per page view. On the Forecast Details page pick a profile
(`Quick Insight / Deep Research / Forecast Assist / Report`) and press
**REQUEST AI OPINION**. The `AIOpinionCard` shows direction, probability, horizon,
catalysts/risks with `evidence_ids`, limitations, and a `CAPPED 20%` badge: the blended
forecast moves at most 20% of the way toward the AI figure, and disagreement with the
deterministic core lowers confidence with a visible warning. Opinions without
`evidence_ids` are rejected-grade and shown for transparency only.

Backend (both spellings accepted — server normalizes display labels to snake_case,
`backend/api/ai.py:72-76` over `PROFILES` in `backend/ai/prompts/__init__.py:23`):

```powershell
curl -X POST "http://localhost:8000/api/ai/insight" -H "Content-Type: application/json" `
  -d '{"symbol":"AAPL","profile":"forecast_assist"}'
# display-label form works identically:
curl -X POST "http://localhost:8000/api/ai/insight" -H "Content-Type: application/json" `
  -d '{"symbol":"AAPL","profile":"Forecast Assist"}'
curl "http://localhost:8000/api/ai/providers/performance?exchange=XNAS&horizon=21"
```

Keys are stored server-side only (Provider Settings → encrypted store, never in the
browser, redacted in logs/audit). Details: `docs/AI_PROVIDERS.md`.

## 4. Backtest Lab (lightweight, v1)

Backtest Lab (`/backtest`) runs walk-forward, time-ordered splits on
corporate-action-adjusted prices and reports Brier score (0 = perfect, 0.25 = coin-flip),
ECE calibration error, the reliability table, and **failures as well as successes** —
never a large backtesting suite (v1 non-goal, spec §1). The UI posts to
`POST /api/backtest/run` (`frontend/src/api/client.ts:577-590`, route
`backend/api/backtest.py:243-249`); history summaries at `GET /api/backtest/{symbol}`
(`backend/api/backtest.py:252-288`).

## 5. Watchlist (FX-gated cross-market comparison)

The Watchlist (`/watchlist`, default `AAPL · MC.PA · ASML.AS · UCB.BR · 600519.SS`) has a
target-currency selector (`USD / EUR / CNY`) and an `FXProvenanceBanner`
(source/as_of/delay/grade/fallback) for the active target.

**FX-gate explanation:** cross-market ranking is refused without *fresh* FX provenance —
envelope present, `fallback_used: false`, `delay_minutes` 0–30, grade A/B (frontend
`isFreshFxProvenance`; backend additionally refuses rates older than 24h or fallback
without explicit `allow_fallback=true` with `FX_PROVENANCE_MISSING`). Fresh FX → ranked
converted table (every figure keeps its quote `ProvenanceBadge`). Stale/missing FX →
`Cross-market comparison unavailable — FX provenance missing` **instead of** ranked
numbers, plus native-currency quotes only. The app never ranks without fresh FX.

```powershell
curl "http://localhost:8000/api/fx/rate?base=EUR&quote=USD"
curl -X POST "http://localhost:8000/api/fx/convert" -H "Content-Type: application/json" `
  -d '{"amount":100,"from":"EUR","to":"USD"}'
curl -X POST "http://localhost:8000/api/fx/rank" -H "Content-Type: application/json" `
  -d '{"symbols":["AAPL","MC.PA","600519.SS"],"target_ccy":"USD"}'
```

Refusal is HTTP `423` with `code FX_PROVENANCE_MISSING`
(`backend/api/fx.py:243-254`; `CODE` in `backend/market_data/fx/convert.py:23`).

## 6. Screener (rank the universe by forecast direction)

The Screener scans the registry universe and ranks by deterministic forecast
`direction_probability` descending (`GET /api/screener`, `backend/api/screener.py:69-80`;
client `getScreener`, `frontend/src/api/client.ts:1373-1387`, own 60s timeout
`SCREENER_TIMEOUT_MS`). Params: `market` (MIC `XNYS|XNAS|XSHG|XPAR|XAMS|XBRU`, omit/`ALL`
for all), `min_direction` (default `0.5`), `horizon` (`1|7|14|21`, default `21`),
`limit` (`1–50`, default `20`). `market=SP500` ranks the 500-stock S&P 500
index universe (constituents span NYSE+Nasdaq, so it is an index group, not
a venue; warmed daily in 10 shards — `.github/workflows/sp500-ingest.yml`).
Bad `horizon`/`market` → `422`
(`backend/api/screener.py:60-65,82-86`). Per-symbol failures appear in `skipped`,
never a batch 500. Cold 500-symbol scans return partial results
(`truncated: true`) inside the serverless budget — retry warm.

```powershell
curl "http://localhost:8000/api/screener?horizon=21&min_direction=0.55&limit=10"
curl "http://localhost:8000/api/screener?market=XPAR&horizon=21&limit=10"
curl "http://localhost:8000/api/screener?market=SP500&horizon=21&limit=20"
```

## 7. Alerts (price / direction rules + scheduled evaluation)

Create a rule, list, patch, delete (`backend/api/alerts.py:252-366`):

```powershell
curl -X POST "http://localhost:8000/api/alerts/" -H "Content-Type: application/json" `
  -d '{"symbol":"AAPL","condition":"price_above","threshold":250}'
curl "http://localhost:8000/api/alerts/?active_only=true"
curl -X PATCH "http://localhost:8000/api/alerts/<alert_id>" -H "Content-Type: application/json" `
  -d '{"is_active":false}'
curl -X DELETE "http://localhost:8000/api/alerts/<alert_id>"
```

`condition ∈ {price_above, price_below, direction_above, direction_below, change_pct_below}`;
`horizon_days ∈ {1, 7, 14, 21}` (direction_* only); unknown symbol → `422`
(`backend/api/alerts.py:261-265`); non-finite threshold → `422`
(`backend/api/alerts.py:184-186`); unknown id → `404` (`backend/api/alerts.py:134-155`).
Evaluation runs every 15 min via GitHub Actions → `GET /api/cron/evaluate`
(`.github/workflows/alerts.yml:12-17,33-39`); manual run `GET|POST /api/cron/evaluate`
(`backend/api/cron.py:338-379`, auth `401` when `CRON_SECRET` set,
`backend/api/cron.py:65-80`).

## 8. Provider keys & budgets (server-side only)

Provider Settings stores keys encrypted at rest — never in the browser, logs, or audit
rows. Allowed providers `gemini|openai|anthropic|xai` (`backend/api/providers.py:39-40`):

```powershell
curl -X POST "http://localhost:8000/api/providers/keys" -H "Content-Type: application/json" `
  -d '{"provider":"gemini","model":"gemini-3.7-flash","api_key":"<secret>"}'
curl "http://localhost:8000/api/providers/keys/status"
curl -X POST "http://localhost:8000/api/providers/budget" -H "Content-Type: application/json" `
  -d '{"provider":"gemini","monthly_usd":20}'
curl "http://localhost:8000/api/providers/budget"
```

Validation is `422` with exact strings (`backend/api/providers.py:43-79,125,189`).
Dashboard: `GET /api/providers/health` (`backend/api/providers.py:13-23`), safe probe
`POST /api/providers/health/test` (`backend/api/providers.py:26-34`).

## 9. AI-disabled mode

Leave all provider keys empty (the default in `infra/docker/.env`). Everything above
except the AI opinion card works unchanged: forecasting runs with `ai_weight = 0`, AI
endpoints report unavailability, and the deterministic core stands alone. This is a
supported operating mode, not a degraded one (spec §7 item 7).

## 10. What v1 does NOT do (non-goals, spec §1)

No live trading, no portfolio construction (the frontend `portfolio/` entry is an
explicit placeholder), no autonomous agents, no deep learning, no full OpenBB
dependency, no large backtesting suite. Scheduled AI reports are the only non-explicit
AI call path.
