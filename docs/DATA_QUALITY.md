# OneMarket Analyzer — Data quality, freshness, retention (Milestone 0)

Spec §§4–6. Every displayed number shows source, timestamp, delay/freshness, grade.

## Quality grades

| Grade | Meaning | UI treatment |
|---|---|---|
| A | Primary source fresh within expected delay; reconciliation passed; no missing fields | Normal display |
| B | Minor staleness (≤2× expected delay) or single-source (no cross-check available) | Show freshness badge |
| C | Stale (>2× delay), fallback/cached data, or ≥1 material field missing | Amber badge + `missing_fields` listed |
| D | Unusable for decisions: circuit open + cache expired, or validation failed | "Unavailable" + reason; block forecasts |

Rules:
- Missing data → `"unavailable"`, never silently zero-filled or forward-filled without flagging.
- Every financial formula records `formula + source fields + fixtures + docs` (M2 acceptance).
- Identical inputs → identical outputs (deterministic core; AI paths pinned to low randomness + evidence hash cache).

## Freshness

- `as_of` = upstream timestamp, not ingest time. `delay_minutes` = known feed delay
  (free/delayed sources declare it; e.g. 15 for US intraday on free tiers).
- Market state per instrument calendar: `open | closed | delayed | stale`.
  - `stale` = now − as_of > max(2× expected delay, 1 trading session for EOD).
- SSE/Euronext use exchange calendars + Yahoo suffixes (`.SS` / `.PA` `.AS` `.BR`);
  currency handling explicit; cross-market ranking **blocked until FX provenance is solid** (M0/M7).

## Provider resilience (Milestone 0)

- Health checks (latency p50/p95, error rate) → Redis-backed circuit breaker per provider:
  `closed → open` after N consecutive failures; half-open probe; `fallback_used: true` while open.
- Rate limiting per provider (token bucket in Redis).
- Reconciliation: when ≥2 sources configured, compare close/volume within tolerance;
  divergence downgrades grade to B/C and is audit-logged.
- Failure simulations + provider contract tests with fixtures run in CI (§6).

## Retention

| Data | Retention | Notes |
|---|---|---|
| Raw intraday bars | 2 years | Then downsample to daily, drop raw |
| Daily bars + adjustments | Indefinite | Corporate-action-adjusted; point-in-time |
| Fundamentals filings snapshots | Indefinite | Point-in-time, restatements preserved |
| Forecasts + feature/model/data versions | Indefinite | Needed for calibration dashboards + leakage tests |
| AI opinions + evidence hashes | 3 years | Token usage logged; prompts versioned |
| Audit logs | 7 years, append-only | Hash-chained; backup-tested |
| Redis cache | TTL 5 min – 24 h by endpoint | Provider-health state persistent (AOF) |

## FX provenance gate

No cross-market comparison/ranking until a dedicated FX source with full
`source/as_of/delay/grade` is wired and `fallback_used=false` at grade A/B.
Until then the Watchlist comparison view stays disabled with an explanatory badge.

---

## M3/M4 appendix: forecast calibration + versioning + retention (normative)

Spec §§5 (M3), §6 testing, §7 definition of done.

### Calibration (walk-forward only)

- Validation is walk-forward with time-ordered splits, corporate-action-adjusted
  prices, and a point-in-time feature store (no leakage: past feature rows are
  invariant to future data; `assert_no_leakage` guard + leakage tests in CI).
- Reported per `(model_version, horizon_days)`: **Brier score**, **calibration
  error (ECE)**, and a **reliability table** (`bin_low/bin_high/count/
  mean_predicted/fraction_positive`). Helpers: `backend/forecasting/calibration/`.
- Targets: direction probability (1/7/14/21 trading days), expected-return range,
  volatility regime, large-drawdown probability, relative performance vs benchmark.
- The performance dashboard shows failures as well as successes; grade-D inputs
  block forecasts (`409 FORECAST_BLOCKED`) instead of emitting uncalibrated numbers.
- Backtests account for liquidity, transaction costs, slippage, delistings,
  restatements, survivorship bias, and market-regime shifts (M3 requirements).

### Calibration snapshots (Phase 2b)

- The nightly `GET /api/cron/calibrate` job replays the live ensemble
  walk-forward over trailing bars and upserts one row per
  `(symbol, horizon_days, model_version, feature_version, data_version)`
  into `calibration_snapshots` (Brier, ECE, 10-bin reliability table,
  per-member `{hit_rate, n}`); `GET /api/forecast/{symbol}` serves the
  latest snapshot's reliability rows as `calibration` (`[]` when none).
- Retention is indefinite, like forecasts: snapshots are keyed to the
  model/feature/data version triple so calibration dashboards and leakage
  tests can always reproduce what a past version claimed.

### Versioning (forecasts are reproducible)

- Every forecast row stores `model_version + feature_version + data_version +
  timestamp` (`forecasts.created_at`) plus `target_date` and `horizon_days`.
- Model artifacts, feature schemas, prompts, and AI schemas are versioned
  **together** (M3 acceptance); the DB enforces one row per run:
  `UNIQUE (instrument_id, horizon_days, target_date, model_version,
  feature_version, data_version)`. Re-runs with new versions append new rows —
  history is never overwritten.
- AI metadata (`ai_provider/ai_model/ai_weight`, `ai_weight ≤ 0.20`) rides on the
  same row so any opinion's influence is auditable; `NULL` provider means AI was
  disabled and the deterministic core stands alone.
- Read the versioned log at `GET /api/audit/forecasts?symbol=AAPL`; AI opinions
  at `GET /api/audit/ai_decisions`. Both payloads carry the `Not investment
  advice` disclosure via the API.

### Retention for forecasts (extends the table above)

| Data | Retention | Notes |
|---|---|---|
| Forecasts + feature/model/data versions | Indefinite | Versioned log; backs calibration dashboards + leakage tests |
| Calibration snapshots (Brier/ECE/reliability per version) | Indefinite | Tied to the model/feature/data version triple |
| Backtest fold results (incl. failures) | Indefinite | Walk-forward folds kept; failures visible, not pruned |
| AI opinions + evidence hashes | 3 years | Token usage logged; prompts + AI schemas versioned with the forecast |
| Audit logs (`forecast.created`, `ai.opinion.*`, `provider.*`) | 7 years, append-only | Hash-chained; verify with `python -m backend.observability.audit_verify` |
| Raw intraday bars | 2 years | Then downsample to daily, drop raw (unchanged) |
