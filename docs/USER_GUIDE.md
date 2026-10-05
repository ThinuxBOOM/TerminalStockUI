# User guide

OneMarket is a research terminal for NYSE, Nasdaq, Shanghai (SSE) and
Euronext Paris, Amsterdam and Brussels. Every number shows where it came
from and how fresh it is, and every forecast shows how accurate it has been.
Not investment advice.

Sign in at `/login` with the account your administrator created. The home
page (`/`) is public; everything else needs an account. Press `Ctrl/⌘ K`
anywhere for the command palette, or `/` to jump to search.

## Reading a forecast

Forecasts cover 1, 7, 14 and 21 trading days and refresh after each close.

- **Return range (80%)**: where the price ended in 80% of comparable past
  periods, from a volatility model. Shown as percentages and prices. In
  testing about 80% of real outcomes landed inside it, so roughly one in ten
  ends below and one in ten above.
- **10%+ drop risk**: the chance of a fall of 10% or more at some point
  within the horizon.
- **Outperformance rank** (US listings): where the stock sits among ~500
  S&P 500 stocks on the model's chance of beating the median stock. "Top 8%"
  means it scores higher than 92% of them. The edge is small: it shows up
  across many stocks, not reliably in any single one. "Pushing it up" and
  "Holding it back" list the factors behind the rank.
- **Chance of rising**: stays close to the historical base rate (stocks rose
  in about 55% of 21-day periods), because no model beat that base rate in
  testing.
- **How accurate is this?**: the measured record for the horizon you're
  viewing: range coverage, drop-risk skill, ranking correlation and the
  top-vs-bottom decile gap. The full record is in the Model Lab.

Freshness badges: **LIVE / DELAYED / CLOSED / STALE** for quotes (free feeds
are usually 15 minutes delayed; "price at …" is when the exchange set the
price) and **DAILY** for numbers built from daily bars.

## Pages

**Overview** (`/app`): market status, benchmark tiles, today's strongest
signals (top and bottom of the ranking), the model's record, data health,
your watchlist, news and market activity.

**Screener** (`/screener`): every stock scored after the last close. Filter
by market, horizon, sector, volatility regime, drop risk and rank; sort any
column; export CSV. Click a row to open the stock.

**Security** (`/security/:symbol`), one page per stock:

- *Overview*: price chart (1W-5Y, indicator overlays), forecast summary and
  risk summary.
- *Forecast*: the range fan chart for every horizon (hover for prices), the
  detail cards above, the measured record and the limitations.
- *Risk*: volatility (realized and forecast), drawdowns, value at risk and
  expected shortfall, beta and correlation, Sharpe/Sortino, liquidity, and a
  **position sizer**: enter your account size and the share you're willing
  to lose, and it sizes the position so that a move to the model's
  10th-percentile outcome costs only that.
- *Fundamentals*: growth, margins, returns, valuation and quality scores
  from filed statements, plus events.
- *News* and *AI*: headlines, and an AI summary of catalysts and risks on
  request (counts toward your daily AI limit; kept separate from the
  measured forecast).

**Watchlist** (`/watchlist`): live prices with each stock's rank, range and
drop risk; switch horizons; import/export JSON; compare prices in one
currency when fresh exchange rates are available. Saved in this browser for
your account.

**Portfolio risk** (`/portfolio`): enter holdings with the amount held in
each (or import your watchlist) and analyze: volatility, 1-day VaR and
CVaR, max drawdown, beta, diversification ratio, each holding's share of
risk vs. share of capital, a correlation heatmap and the worst days. Saved
in this browser for your account.

**Model Lab** (`/model`): how the live model did on years it never saw,
per horizon: range coverage by year, average return by model decile,
ranking correlation by year, drop-risk and direction skill, and a per-stock
lookup.

**Data health** (`/providers`): status, latency and error rate per data and
AI provider. Administrators manage AI provider keys and budgets here.

**Account** (`/account`): your email and role, and sign-out (ends your
sessions on every device).

## API access

The web app uses the same API. Get a token, then send it as a bearer token
(tokens expire after 15 minutes):

```bash
BASE=https://onemarket.example.com
TOKEN=$(curl -s -X POST "$BASE/api/auth/login" -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"..."}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
AUTH="Authorization: Bearer $TOKEN"

curl -H "$AUTH" "$BASE/api/forecast/AAPL/all"
curl -H "$AUTH" "$BASE/api/screener?market=US&horizon=21&sort=out_rank&limit=20"
curl -H "$AUTH" "$BASE/api/risk/AAPL"
curl -H "$AUTH" -X POST "$BASE/api/risk/portfolio" -H 'Content-Type: application/json' \
  -d '{"holdings":[{"symbol":"AAPL","weight":60},{"symbol":"MC.PA","weight":40}]}'
```

Alerts are available through the API (no page yet). They belong to the user
who creates them and are evaluated every 15 minutes:

```bash
curl -H "$AUTH" -X POST "$BASE/api/alerts/" -H 'Content-Type: application/json' \
  -d '{"symbol":"AAPL","condition":"price_above","threshold":250}'
curl -H "$AUTH" "$BASE/api/alerts/?active_only=true"
```

Conditions: `price_above`, `price_below`, `direction_above`,
`direction_below`, `change_pct_below`. Fired alerts are posted to
`ALERTS_WEBHOOK_URL` when the operator sets one.

Request and response shapes: `docs/API_CONTRACT.md`.
