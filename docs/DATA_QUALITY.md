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

## Forecast engine v4: method and measured accuracy

Code: `backend/forecasting/v4/`. Training: `scripts/train_models.py`
(weekly `train` job). Live record: Model Lab, `GET /api/forecast/model`.

### What is forecast

For each stock and horizon (1, 7, 14, 21 trading days):

| Output | Model | Trained on |
|---|---|---|
| Return range (5/10/25/50/75/90/95th percentiles) | log-HAR volatility forecast (trailing 5/21/63/252-day variances), times empirical quantiles of volatility-standardized returns | all stocks pooled |
| 10%+ drop probability | logistic regression on the forecast volatility | all stocks pooled |
| Outperformance (beats the day's median stock) and rank | L2 logistic regression on 20 features ranked across stocks each day: 1/3/6-month and 12-1 momentum, 1-week and 1-day reversal, distance from 50/200-day averages and 52-week high, RSI, volatility level and ratios, abnormal volume and range, 3-month return vs. market and vs. sector | S&P 500, cross-sectional |
| Chance of rising | same features, blended toward the training base rate as far as nested walk-forward supports | S&P 500 |

Models are plain coefficients and quantile grids stored as JSON (no pickles).

### How it is tested

- Ten years of daily S&P 500 bars (~1.1M stock-days, 2017-2026).
- Walk-forward by calendar year: each year from 2021 is predicted by models
  trained only on earlier dates, with an embargo of `horizon` sessions so no
  training label overlaps the test period.
- The base-rate blend is chosen *nested* (each test year uses a blend fit on
  earlier years only), so the reported skill is not tuned on itself.
- Uncertainty: block bootstrap over calendar months (stocks on the same day
  move together, so rows are not independent). Ranking t-statistics use
  every h-th date so overlapping returns are not double-counted.
- Universe is today's S&P 500, so results carry survivorship bias. No
  trading costs are modelled; these are forecasting metrics, not a strategy.

### Results (model v4-20261001-1507)

| Metric | 1 day | 7 days | 14 days | 21 days |
|---|---|---|---|---|
| 80% range coverage (target 80%) | 79.7% | 80.0% | 79.5% | 79.3% |
| Interval score vs. per-stock trailing window | 2% better | 2% better | 3% better | 4% better |
| Drop-risk Brier skill vs. base rate | -1.0% | +4.5% | +5.0% | +5.0% |
| Ranking IC (rank correlation with later return) | 0.023 | 0.025 | 0.026 | 0.025 |
| Ranking IC t-statistic | 4.6 | 2.4 | 1.2 | 1.3 |
| Top-minus-bottom decile, per period | +0.06% | +0.32% | +0.59% | +0.76% |
| Chance-of-rising Brier skill vs. base rate | +0.0% | +0.0% | -0.1% | -0.1% |

Reading it:

- **Ranges are well calibrated** at every horizon, and better than the
  old per-stock method, which under-covered at 21 days (77.9%, with 11.9%
  of outcomes below the band instead of 10%). Coverage dips in volatility
  shocks (2022: 73.8% at 21 days) and recovers as volatility catches up.
- **Drop risk** beats the base rate by ~5% from 7 days out; 1-day 10% drops
  are too rare to forecast.
- **The ranking edge is small but consistent**: significant at 1 and 7
  days, positive but noisier at 14 and 21. Average 21-day return rises from
  +0.38% in the lowest-ranked decile to +1.15% in the highest. 2022 (a
  momentum crash) was negative.
- **Direction has no edge.** Pooled models on these features, including a
  gradient-boosted tree (which did worse: -2.7% to -9.2% skill), did not
  beat the base rate. The app therefore shows the base rate with at most a
  small tilt, and says so.

### History: why v3 was replaced

The v3 ensemble fit small models per stock on ~400 rows each. Measured
walk-forward over 100 stocks (2024-2026) it scored *worse* than the base rate
at every horizon (Brier skill -0.011 to -0.031), and its per-stock isotonic
calibration made that worse still (-0.021 to -0.101). Pooling across stocks
and ranking cross-sectionally is what turned noise into a measurable signal.

### Data hygiene

- Daily bars are stored at the exchange's local midnight. Providers that
  stamp daily candles at 00:00 UTC are normalized on ingest, so one session
  can't be stored twice (migration 0013 repairs older rows).
- Benchmark indices are registered under their own venue, so their session
  days line up with the stocks they benchmark.

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
| Model bundles (`model_artifacts`, with their walk-forward report) | Indefinite | One row per weekly retrain; the active one is flagged |
| Daily scores (`forecast_scores`) | Latest only | Overwritten daily; history is in `forecasts` |
| AI opinions + evidence hashes | 3 years | Token usage logged; prompts + AI schemas versioned with the forecast |
| Audit logs (`forecast.created`, `ai.opinion.*`, `provider.*`) | 7 years, append-only | Hash-chained; verify with `python -m backend.observability.audit_verify` |
| Raw intraday bars | 2 years | Then downsample to daily, drop raw (unchanged) |
