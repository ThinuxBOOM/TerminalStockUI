# Free Market-Data Sources (researched 2026-09-15)

Reliable FREE sources for the quotes this app needs for analysis +
predictions across **XNYS/XNAS (US)**, **XSHG (SSE)**, and
**XPAR/XAMS/XBRU (Euronext)**. All claims below were verified against
vendor docs/pricing pages in September 2026; re-check before signing
anything, free tiers move.

Fail-closed rule (NO FALLBACKS): every source either serves live data or the
request raises (`ProviderError` → HTTP 502/503) — never a flagged stub, never
stale/cached-as-fresh, never a fabricated price. Provenance + grade describe
live data only; new providers only extend the live chain. See
`docs/FAIL_CLOSED_CONTRACT.md`.

## Recommended priority per market (implemented in `MarketDataService`)

| Market (MIC) | Priority (first live wins, else 502) | Notes |
|---|---|---|
| XNYS / XNAS (US) | **Alpaca → yfinance → Finnhub → TwelveData → Stooq** → raise | 3 independent real-time legs + 2 delayed legs |
| XSHG (SSE) | **yfinance (`.SS`) → AKShare** → raise | Finnhub/TwelveData/Stooq/Alpaca never routed here (US/EU-only or wrong feed) |
| XPAR / XAMS / XBRU (Euronext) | **yfinance → Stooq** → raise | Free Finnhub/TwelveData tiers are EOD-only outside the US, so they stay out of the Euronext live path |
| FX (USD/EUR/CNY) | **Frankfurter → yfinance FX** → raise | Daily ECB reference; reconciled flag drives grade A/B |

Outage behavior (nothing live): raise `ProviderError` (HTTP 502) with the
provider name + symbol. No preference loop, no snapshot cover, no stub.

## Source ranking (free tiers)

Ranked by reliability × coverage × free-tier fit for THIS app.

### 1. Stooq — delayed gap-filler, US + Euronext (no key) ✅ in chain
- **Delay:** ~15 min (intraday bars delayed the same; daily/weekly/monthly EOD).
- **Rate limits:** undisclosed daily quota per key/IP. Quota breach returns
  **HTTP 200 with an `"Exceeded the daily hits limit"` body** (never 429) —
  the provider now detects this body explicitly (`stooq.py`).
  As of early 2026, `/q/d/l/` (history) needs an `apikey` from an on-site
  CAPTCHA flow; the `/q/l/` snapshot used here stays keyless.
- **ToS:** free market data for personal use; bulk re-download discouraged
  (use the `/db/` snapshots + local cache for history).
- **Reliability:** high; stable CSV endpoint since the early 2000s. Quirks:
  `N/D` rows for unknown symbols, empty 200 bodies under abuse.
- **Covers:** XNYS/XNAS (`.us`), XPAR (`.fr`), XAMS (`.nl`), XBRU (`.be`).
  NOT routed for XSHG (Yahoo `.SS` + AKShare own SSE).

### 2. Yahoo Finance via yfinance — default source, all markets (no key) ✅ in chain
- **Delay:** ~15 min for US/EU/CN (unauthenticated quotes are delayed).
- **Rate limits:** unpublished, per-IP; sustained scraping trips **429**
  after a few hundred requests; `401 Invalid Crumb` waves since the 2024
  auth tightening (Feb 2025 page overhaul broke parsers again). Mitigation:
  the chain (one live feed = one call), 60s quote cache, per-provider breaker.
- **ToS:** personal use only; automated collection is against Yahoo's terms
  without permission. Not affiliated with Yahoo.
- **Reliability:** medium — broadest coverage (US + `.SS` + `.PA`/`.AS`/`.BR`
  + FX `=X` tickers) but the flakiest leg. That is exactly why the chain
  exists.

### 3. Alpaca Basic (IEX) — real-time US (key) ✅ in chain
- **Delay:** 0 (real-time **IEX only** — single exchange, not full NBBO/SIP;
  badged live-but-partial).
- **Rate limits:** 200 req/min market data on free Basic; 10,000/min on
  Algo Trader Plus ($99/mo, full SIP).
- **ToS:** brokerage account required (paper is fine); keys header-only,
  never logged.
- **Reliability:** high. US-only by construction.

### 4. Finnhub free — real-time US (key) ✅ NEW in chain
- **Delay:** 0 for US (`/quote` is real-time on free).
- **Rate limits:** **60 calls/minute** free; WebSocket 50 symbols.
- **Coverage caveat (why US-only here):** real-time international feeds are
  paid; free international is **EOD-only**. Euronext/SSE stay on
  yfinance/Stooq/AKShare.
- **ToS:** free + all self-serve tiers are **personal use**; commercial /
  professional use needs Finnhub's written approval; redistribution is
  Enterprise-only.
- **Endpoint gaps:** `/quote` carries **no volume** → always flagged in
  `missing_fields`; `0` means "no data" (never a price).
- **Env:** `FINNHUB_API_KEY` (or `FINNHUB_TOKEN`).

### 5. TwelveData Basic — real-time US (key) ✅ NEW in chain
- **Delay:** 0 for US (real-time default feed ≈5% of US volume from
  license-light venues — live-but-partial, same badge philosophy as IEX).
- **Rate limits:** **8 credits/min + 800/day** free (`/quote` = 1 credit);
  paid removes the daily cap (Grow $29/mo 55/min … Ultra $999/mo).
- **Coverage caveat (why US-only here):** free = US + forex + crypto only;
  Euronext EOD/real needs Grow ($29/mo, 20+ markets) / Pro ($99/mo, 70+
  markets incl. real-time EU).
- **ToS:** free = internal non-display; display/commercial needs paid.
- **Quirks:** errors may arrive as HTTP 200 + `{"status":"error",...}`
  (429 quota / 401 key / 404 symbol) — the provider parses the body, not
  just the status. Exchange `datetime` is zone-naive US/Eastern, parsed to
  UTC (never trusted as UTC).
- **Env:** `TWELVEDATA_API_KEY` (or `TWELVE_DATA_API_KEY`).

### 6. AKShare — SSE spot/history (no key) ✅ in chain (SSE only)
- Free-first CN source complementing yfinance `.SS`; 60s memoised spot
  table; always CNY. Optional dep, stub fallback.

### 7. Frankfurter / ECB eurofxref — FX (no key) ✅ in chain (FX only)
- Daily ECB reference (~16:00 CET, business days), no quotas, open source
  + self-hostable; yfinance `EURUSD=X`-style secondary; deterministic stub
  triangle (EURUSD 1.08 / USDCNY 7.25). Right tool for daily-bar FX;
  wrong tool for intraday FX.

## Financial statements (free feed for fundamentals/quality/valuation)

Price vendors only ship OHLCV, so `GET /api/analytics/{symbol}` used to
serve `fundamentals/quality/valuation` as honest "unavailable"
(`EMPTY_STATEMENTS`). Two keyless legs now feed those sections
(`backend/market_data/statements/`, routed by `resolver.get_statements`):

| Leg | Covers | Key? | Source of truth |
|---|---|---|---|
| **SEC EDGAR XBRL** (`data.sec.gov/api/xbrl/companyfacts`) | US (XNYS/XNAS, incl. 20-F/40-F foreign filers) | no (mandatory `User-Agent` contact via `STATEMENTS_CONTACT`, 10 req/s fair use) | The filed 10-K/20-F/40-F itself; updated <1 min after acceptance |
| **yfinance annuals** (income/balance/cash-flow) | Global incl. SSE + Euronext; US fallback | no | Yahoo compilations (thinner outside the US — gaps stay "unavailable") |

Chain: US → SEC EDGAR, yfinance on SEC failure · SSE/Euronext →
yfinance only (EDGAR is never asked — no CIK coverage there). Annual
10-K/20-F/40-F facts only (350–380-day flows); restatements resolve to
latest-filed-wins; every metric anchors on revenue's fiscal ends so
margins never mix periods. Derived in the resolver (shared by both legs):
working capital, current ratio, asset turnover, gross margin (+ priors),
total debt, FCF (`base_fcf`), effective tax rate, market cap from the
quote. 24h cache (statements move quarterly).

Honest limits: WACC stays "unavailable" (needs market-implied
cost-of-equity/debt, which filings cannot supply); non-US coverage is
only as deep as Yahoo's statements; no point-in-time restatement
archive beyond latest-filed-wins (see `docs/DATA_QUALITY.md` retention
row for filings snapshots).

## Evaluated but NOT wired (too thin for the live chain)

| Source | Free tier (2026-09) | Verdict |
|---|---|---|
| **Alpha Vantage** | 25 req/day (+≈5/min throttle) | Too thin for any chain position (a 50-symbol watchlist exhausts it in one pass). Research/backfill-only. |
| **Nasdaq Data Link (WIKI EOD)** | Free with key (50k/day reg.) | WIKI feed deprecated since 2018, EOD, US-only, unmaintained. Not a quote source. |
| **Yahoo chart API direct** (`query1.finance.yahoo.com`) | Same data as yfinance | Same ToS/rate-limit profile as #2 with more maintenance (crumb handshake). No gain over yfinance lib. |

## Rate-limit / delay cheat sheet

| Provider | Key? | Delay (as served) | Free rate limit | Covers (in OUR chain) |
|---|---|---|---|---|
| yfinance | no | 15 min | unpublished per-IP (~100s reqs → 429) | US, SSE, Euronext, FX |
| AKShare | no | 15 min | n/a (CN endpoints) | SSE |
| Alpaca Basic | yes | 0 (IEX partial) | 200 req/min | US |
| Finnhub free | yes | 0 (US) | 60 req/min | US |
| TwelveData Basic | yes | 0 (US partial) | 8/min + 800/day | US |
| Stooq | no | 15 min | undisclosed daily quota (200-body!) | US, Euronext |
| Frankfurter/ECB | no | daily fix | none (abuse throttle only) | FX |
| SEC EDGAR XBRL | no (+`User-Agent`) | filings (<1 min) | 10 req/s fair use | US statements |
| yfinance annuals | no | annual | unpublished per-IP | statements (all markets) |

Local `RateLimiter` buckets mirror the free tiers (Finnhub 1 rps/burst 5;
TwelveData 8/60 rps/burst 8; Stooq 2 rps/burst 4; Alpaca 3 rps/burst 6);
production enforces the same quotas Redis-backed.

## Future-proofing: subscription tiers (not implemented)

`MarketDataService.get_quote` / `get_quotes_many` accept nullable
`user_id` / `tier` (accepted, inert today). Provider gating belongs in the
service chain (draft: Free → keyless only; Silver → + shared-key free
tiers; Gold/Platinum → + paid SIP / real-time Euronext / full TwelveData
markets with per-user keys), never inside providers. Paid providers slot
in as new `*_provider` constructor args (None = skipped).
