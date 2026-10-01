# User guide

OneMarket Analyzer is a research terminal for NYSE, Nasdaq, Shanghai (SSE)
and Euronext Paris/Amsterdam/Brussels. Every number shows where it came
from, when, and how fresh it is. Forecasts are experimental probabilities,
not investment advice.

Sign in at `/login` with the account your administrator created. The
landing page (`/`) is public; everything else needs an account.

## Reading the badges

- **Source / grade**: which provider served the number and a quality grade
  (A best to D).
- **LIVE / DELAYED / CLOSED / STALE**: freshness of a quote. Free feeds are
  usually 15 minutes delayed. `price at …` next to a quote is when that price
  was set on the exchange.
- **DAILY**: built from daily bars, which are as current as the last
  completed trading session.
- **EXPERIMENTAL**: on every forecast. The models' real track record is in the
  Backtest Lab; treat probabilities as unvalidated until it says otherwise.

When a source returns no live data the app says "unavailable" rather than
showing an old or made-up number.

## Pages

**Overview** (`/app`): market open/closed strip, "What should I look at
today?" (top forecast signals per market, a starting point for research, not
orders), your watchlist, market news, and collapsible sections for market
indexes, market activity and data health.

**Discover** (`/search`): one search across all markets by ticker, name or
provider symbol (`AAPL`, `Moutai`, `600519.SS`, `MC.PA`). A market filter
narrows the results; when several instruments match, you pick, the app never
guesses.

**Security Brief** (`/security/:symbol`): price in the native currency,
chart (1D to 5Y, with SMA/EMA/RSI/MACD/Bollinger/VWAP/ATR overlays), events,
analytics snapshot and the forecast card.

**Forecast** (`/forecast/:symbol`): the deterministic forecast for 1, 7, 14
or 21 trading days: probability of rising, expected return range, volatility
regime, drawdown probability, model versions, calibration history and
limitations. You can also request an AI opinion here (see below).

**Screener** (`/screener`): ranks a market (or the S&P 500 universe) by
forecast probability. Per-symbol failures are listed, not hidden.

**Backtest** (`/backtest`): walk-forward backtests with Brier score
(0 is perfect, 0.25 is a coin flip), calibration error and a reliability
table, failures included.

**Watchlist** (`/watchlist`): symbols you follow, stored in this browser.
Cross-market ranking in one currency only appears when fresh exchange rates
are available; otherwise you see native-currency quotes and an explanation.

**Data Health** (`/providers`): status, latency and error rate per data and
AI provider. Administrators also manage AI provider keys and budgets here.

**Account** (`/account`): your email and role, and sign-out (which ends your
sessions on every device).

## AI opinions

AI never runs on page views. On the Forecast page choose a profile (Quick
Insight, Forecast Assist, Deep Research, Report) and request an opinion. The
opinion lists catalysts and risks tied to evidence, and its influence on the
blended forecast is capped at 20%. This needs an AI provider key configured
by an administrator; each user has a daily limit (`AI_DAILY_CALLS_PER_USER`).

## API access

The web app uses the same API. Get a token, then send it as a bearer token
(tokens expire after 15 minutes):

```bash
BASE=https://onemarket.example.com
TOKEN=$(curl -s -X POST "$BASE/api/auth/login" -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"..."}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
AUTH="Authorization: Bearer $TOKEN"

curl -H "$AUTH" "$BASE/api/market_data/quote?symbol=600519.SS"
curl -H "$AUTH" "$BASE/api/forecast/AAPL?horizon=21"
curl -H "$AUTH" "$BASE/api/screener?market=XPAR&horizon=21&limit=10"
```

Alerts are available through the API only (no page yet). They belong to the
user who creates them and are evaluated every 15 minutes:

```bash
curl -H "$AUTH" -X POST "$BASE/api/alerts/" -H 'Content-Type: application/json' \
  -d '{"symbol":"AAPL","condition":"price_above","threshold":250}'
curl -H "$AUTH" "$BASE/api/alerts/?active_only=true"
curl -H "$AUTH" -X DELETE "$BASE/api/alerts/<alert_id>"
```

Conditions: `price_above`, `price_below`, `direction_above`,
`direction_below`, `change_pct_below`. Fired alerts are posted to
`ALERTS_WEBHOOK_URL` when the operator sets one.

Full request/response shapes: `docs/API_CONTRACT.md`. Market notes:
`docs/SSE_NOTES.md`, `docs/EURONEXT_NOTES.md`.
