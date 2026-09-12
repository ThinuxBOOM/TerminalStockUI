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
   `ProvenanceBadge`.

Backend equivalents: `GET /api/instruments/search?q=Moutai&market=XSHG`,
`GET /api/market_data/quote?symbol=600519.SS&market=XSHG`. Full shapes:
`docs/API_CONTRACT.md` (+ M6/M7 appendices), `docs/SSE_NOTES.md`, `docs/EURONEXT_NOTES.md`.

## 2. Forecast details (deterministic core)

Open **FORECAST DETAILS →** (`/forecast/:symbol`). Horizons are trading days `5 / 21 / 63`
only — any other value is rejected. The page shows direction probability, expected-return
interval (low/mid/high), volatility regime, drawdown probability, model/feature/data
versions, evidence IDs, calibration history (reliability table), limitations, and the
disclosure string.

Backend (call directly; the UI forecast fetcher is being aligned to these routes):

```powershell
# deterministic ensemble forecast (symbol is a path segment, horizon is a query param)
curl "http://localhost:8000/api/forecast/AAPL?horizon=21"
curl "http://localhost:8000/api/forecast/600519.SS?horizon=21"
curl "http://localhost:8000/api/forecast/MC.PA?horizon=63"
# analytics snapshot + lightweight walk-forward backtest
curl "http://localhost:8000/api/analytics/AAPL"
curl -X POST "http://localhost:8000/api/backtest/run" -H "Content-Type: application/json" `
  -d '{"symbol":"AAPL","horizons":[21]}'
```

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

Backend (profile keys are snake_case):

```powershell
curl -X POST "http://localhost:8000/api/ai/insight" -H "Content-Type: application/json" `
  -d '{"symbol":"AAPL","profile":"forecast_assist"}'
curl "http://localhost:8000/api/ai/providers/performance?exchange=XNAS&horizon_days=21"
```

Keys are stored server-side only (Provider Settings → encrypted store, never in the
browser, redacted in logs/audit). Details: `docs/AI_PROVIDERS.md`.

## 4. Backtest Lab (lightweight, v1)

Backtest Lab (`/backtest`) runs walk-forward, time-ordered splits on
corporate-action-adjusted prices and reports Brier score (0 = perfect, 0.25 = coin-flip),
ECE calibration error, the reliability table, and **failures as well as successes** —
never a large backtesting suite (v1 non-goal, spec §1). Until the UI fetcher is aligned,
post to `/api/backtest/run` directly (see §2).

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

## 6. AI-disabled mode

Leave all provider keys empty (the default in `infra/docker/.env`). Everything above
except the AI opinion card works unchanged: forecasting runs with `ai_weight = 0`, AI
endpoints report unavailability, and the deterministic core stands alone. This is a
supported operating mode, not a degraded one (spec §7 item 7).

## 7. What v1 does NOT do (non-goals, spec §1)

No live trading, no portfolio construction (the frontend `portfolio/` entry is an
explicit placeholder), no autonomous agents, no deep learning, no full OpenBB
dependency, no large backtesting suite. Scheduled AI reports are the only non-explicit
AI call path.
