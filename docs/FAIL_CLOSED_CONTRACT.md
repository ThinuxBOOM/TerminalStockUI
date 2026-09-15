# Fail-Closed Contract (V1 — NO FALLBACKS)

> Delays are fine when unavoidable. Stale data and fallbacks are not.
> Everything below is enforced on the wire and covered by tests. V2 starts here.

## Rule

- Live data or an honest error. Never `200` with synthetic / stub / stale /
  cached-as-fresh data. Never `fallback_used:true` on the wire.
- `fallback_used` is always `false` on success. Any lingering fallback flag is
  a contract violation and raises instead of serving.

## Status codes

| Situation | Code | Example |
|---|---|---|
| No live quote / bars (all providers down) | `502` | `yfinance: no live quote for AAPL (all providers unavailable)` |
| No live bars (DB thin/empty, live fetch missed) | `502` | `no live bars for X (DB thin/empty, live fetch missed)` |
| Days-old bars (DB stale, refresh missed) | `502` | `no live bars for X (DB thin/empty/stale, live fetch missed)` |
| No live FX rate | `502` | `no live rate for EUR/USD (upstream unavailable)` |
| AI with no key | `423` | `AI disabled: no API key configured for gemini (...)` |
| AI live-call failure (timeout/network, stub refused) | `502` | `AI insight failed: live model call unsuccessful` |
| Stale/missing FX on rank | `423` | `FX_PROVENANCE_MISSING` |
| Bad symbol / horizon / profile / market | `422` | `horizon must be one of [5, 21, 63]` |
| Unknown instrument / job | `404` | `unknown job_id` |

## Per surface

- **Quotes/bars** (`MarketDataService`): first live quote wins
  (US: Alpaca → yfinance → Finnhub → TwelveData → Stooq; SSE: yfinance → AKShare;
  Euronext: yfinance → Stooq). Nothing live → raise. Bars: DB-first with coverage
  gate (`min(n,100)` rows) AND a calendar-aware freshness gate (`1d` only):
  the latest bar date must cover the last completed trading session
  (`last_completed_trading_day`: weekends expect Friday, pre-open Monday
  expects Friday, post-close Monday expects Monday; 60min post-close grace).
  Stale DB bars trigger a live refresh; when refresh also yields nothing
  fresh the request raises (`502`) — days-old history is never served.
  A 15min feed delay is fine; a 2-day-old tape is not. No synthetic bars,
  no snapshot cover, no future-dated quotes.
- **AI** (`/api/ai`): no key → `423` before any evidence work. Live-call failure /
  stub → `502` via the wire guard (`_refuse_stub_opinion`). `ai_enabled:false`
  skips the model and returns the deterministic blend (no fake opinion).
- **Forecast/backtest/market-index/liquidation/screener/markets**: no bars →
  `502` (never fabricated envelopes). Per-symbol failures degrade to `skipped`
  (honest omission, never fallback rows). Empty scans return honest non-fallback
  envelopes.
- **FX**: Frankfurter → yfinance FX → raise. No stub table served. Reconciler
  abstains silently when ECB is down (unreconciled live rate, never blocked).
- **Workers**: jobs return `ok:false` + `errors` when nothing was done — never a
  fake success. Envelopes carry `fallback_used:false`.
- **Frontend**: missing provenance throws to `ErrorState` (never synthesized).
  `422`/`502` from `/api/markets/{mic}/index` throw immediately (only `404`/`501`
  fall through to the bars chain). Liquidity-history has no client placeholder —
  errors propagate. Stale/fallback data renders `ErrorState` with retry, never a
  table + banner. Provider health never defaults to ok-with-0ms: uncalled
  providers read `unknown`/`unconfigured` with no latency figure ("no samples
  yet"); only measured samples are shown.

## Provider health honesty

- Latency figures are measured samples only. Zero-call providers report
  `latency_p50_ms/p95_ms: null` (never `0.0`); cache serves are not recorded
  as provider calls; unknown/no-work probes report `latency_ms: null`.
- State is `up|degraded|down|unknown|unconfigured` — the dashboard maps it
  verbatim and never invents `"ok"` for providers with no samples.

## Provenance

Every success carries `{source, as_of, delay_minutes, quality_grade,
fallback_used:false, missing_fields}`. `missing_fields` lists honestly absent
fields (e.g. `liquidation-feed` on the PROXY). Grade comes from `grade_quality`
on live data only.
