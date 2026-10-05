# OneMarket

Stock research terminal with forecasts you can check. For NYSE, Nasdaq,
Shanghai and Euronext (Paris, Amsterdam, Brussels):

- **Forecasts** for 1, 7, 14 and 21 trading days: the likely return range,
  the chance of a 10%+ drop, an outperformance rank against the S&P 500, and
  the chance of rising, each shown next to its measured accuracy.
- **Screener** over every stock scored after each close.
- **Risk**: volatility, drawdowns, VaR/CVaR, beta and position sizing per
  stock; correlations and risk contributions for a portfolio.
- **Research**: charts with indicators, fundamentals, news and an optional
  AI assistant.
- **Model Lab**: the walk-forward record of the live model.

Every number shows its source and freshness. Every page and API route needs
an account. Not investment advice.

## How good are the forecasts?

The forecast engine (v4) trains on ten years of daily data for ~500 S&P 500
stocks and is tested walk-forward: each year from 2021 is predicted by a
model trained only on earlier years. Current record (`docs/DATA_QUALITY.md`):

| | 1 day | 7 days | 14 days | 21 days |
|---|---|---|---|---|
| 80% range contains | 79.7% | 80.0% | 79.5% | 79.3% |
| Drop-risk skill vs. base rate | -1.0% | +4.5% | +5.0% | +5.0% |
| Ranking rank correlation (t-stat) | 0.023 (4.6) | 0.025 (2.4) | 0.026 (1.2) | 0.025 (1.3) |
| Chance of rising vs. base rate | ≈ 0 | ≈ 0 | ≈ 0 | ≈ 0 |

Ranges and drop risk are well calibrated, and the ranking has a small,
real edge across many stocks. On plain up/down, no model we tested beat the
historical base rate, so the app shows that base rate instead of a guess.
The model retrains weekly and these numbers update with it (Model Lab,
`/api/public/model`).

## Deploy on a Linux server

Prerequisites: Docker with the compose plugin, a DNS record pointing at the
server, ports 80 and 443 open, a Supabase project, and ~2 GB of free RAM
for the weekly model training.

```bash
git clone <repo> onemarket && cd onemarket
cp .env.example .env          # fill in DOMAIN, SECRET_KEY, CRON_SECRET, DATABASE_URL
docker compose up -d --build
docker compose exec backend python scripts/migrate.py baseline 0010   # only if 0001-0010 were applied by hand
docker compose exec backend python scripts/migrate.py up
docker compose exec backend python scripts/create_user.py you@example.com --admin
```

The app forecasts immediately with the model bundled in the repository. The
scheduler scores every stock daily and retrains weekly; to do either now:
`docker compose exec scheduler run-job predict` (or `train`). Full guide:
`docs/OPERATIONS.md`.

## Develop

```bash
pip install -r backend/requirements-dev.txt
APP_ENV=development uvicorn backend.app:app --reload     # SQLite ./onemarket.db
cd frontend && npm ci && npm run dev                      # proxies /api and /health to :8000
APP_ENV=development python scripts/create_user.py dev@example.com --admin
APP_ENV=development python -m backend.forecasting.v4.jobs predict   # score the universe
```

Tests: `python -m pytest backend/tests` (hermetic: temp DB, no network) and
`cd frontend && npm test && npm run build`. CI also applies every migration
to Postgres and runs Playwright against the booted stack (`docs/TESTING.md`).

## Docs

`docs/OPERATIONS.md` (running it) · `docs/USER_GUIDE.md` ·
`docs/DATA_QUALITY.md` (model and data accuracy) · `docs/API_CONTRACT.md` ·
`docs/DB_SCHEMA.md` · `docs/SECURITY.md` · `docs/TESTING.md`

Market data comes from free and personal-use sources (yfinance; optional
Alpaca/Finnhub/TwelveData keys). Check their terms before sharing access.
