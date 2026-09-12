# Euronext notes (M7) — Paris / Amsterdam / Brussels via Yahoo `.PA` / `.AS` / `.BR`

Seed symbols (see `backend/instruments/registry.py`):

- `MC.PA` — LVMH Moet Hennessy Louis Vuitton SE (`XPAR`, `EUR`, `FR0000121014`)
- `ACA.PA` — Credit Agricole S.A. (`XPAR`, `EUR`, `FR0000045072`)
- `ASML.AS` — ASML Holding N.V. (`XAMS`, `EUR`, `NL0010273215`)
- `UCB.BR` — UCB S.A. (`XBRU`, `EUR`, `BE0003739530`)

## `.PA` / `.AS` / `.BR` identity

- Canonical provider (Yahoo-style) symbols: Paris `<base>.PA`, Amsterdam
  `<base>.AS`, Brussels `<base>.BR` (see `backend/instruments/calendars.py`:
  `suffix_for_mic("XPAR") == ".PA"`, `provider_symbol_for`).
- Identity is `(exchange_mic, exchange_symbol)` — never the bare ticker.
  `MC` + `market=XPAR` and `MC.PA` resolve to the same instrument; the UI
  always displays the provider form (`MC.PA`, `ASML.AS`, `UCB.BR`).
- Search (`GET /api/instruments/search`) matches `exchange_symbol`,
  `provider_symbol`, and `company_name` (so `LVMH` works);
  `?market=XPAR|XAMS|XBRU` scopes to one Euronext venue, omit/`ALL` for
  cross-market.
- Quote (`GET /api/market_data/quote?symbol=MC.PA&market=XPAR`) returns the
  canonical `instrument` block + `currency: EUR` + `market_state` +
  `ambiguous/candidates` (surfaced, never guessed). Optional `target_ccy`
  asks for a converted preview; native `price` stays `EUR`.

## Sessions: 09:00–17:30 continuous

- Euronext venues trade one continuous session 09:00–17:30 exchange-local
  (`Europe/Paris`, `Europe/Amsterdam`, `Europe/Brussels`). No lunch break
  (unlike XSHG 11:30–13:00).
- `market_state` is calendar-aware (`open | closed | delayed | stale` from
  the exchange calendar layered over provenance freshness). Frontend
  fallback rule: missing `market_state` → derive `open | delayed | stale`
  from provenance only; `closed` / `lunch` are never synthesized
  client-side.
- Currency: all Euronext money is `EUR`, formatted with
  `Intl.NumberFormat('de-DE', { style: 'currency', currency: 'EUR' })` → `€`
  (via the shared `CurrencyValue` component). No hardcoded prices or FX.

## Holiday stub limits

- Exchange-holiday calendars are stubs in v1: Euronext `is_holiday()` covers
  weekends via `is_trading_day()` only (the lunar/fixed-rule stub is XSHG
  scoped). Outside scheduled sessions `market_state` may report
  `delayed`/`stale` from provenance age rather than a calendar-exact
  `closed`.
- Do not treat `open` as a trading signal; it means "fresh within expected
  delay". Calendar-exact open/closed comes only when the backend sends it
  explicitly. Production MUST replace stubs with a licensed Euronext
  calendar.

## FX gate rules (M7/M8 — normative)

- No cross-market conversion or ranking without **fresh FX provenance**:
  envelope present, `fallback_used: false`, `delay_minutes` 0–30,
  `quality_grade` A/B (see `isFreshFxProvenance` in
  `frontend/src/api/client.ts`).
- `POST /api/fx/rank` refuses stale/missing FX with
  `{"error": {"code": "FX_PROVENANCE_MISSING", ...}}` (HTTP 409). The
  client rethrows untouched (`isFxProvenanceMissingError`) so the Watchlist
  can gate.
- Watchlist behavior (`frontend/src/pages/WatchlistPage.tsx`):
  - Target-ccy selector `USD / EUR / CNY`; FX provenance banner shows
    `source / as_of / delay / grade / fallback` for the active target.
  - Fresh FX → ranked table (converted via `Intl.NumberFormat`, every figure
    keeps its `ProvenanceBadge`).
  - Stale/missing FX → red gate panel
    `Cross-market comparison unavailable — FX provenance missing` INSTEAD OF
    ranked numbers, plus native-currency quotes only (no conversion
    applied). Never rank without fresh FX.

## Acceptance checklist (M7 Euronext + FX-gated Watchlist)

- [ ] Search `LVMH` / `MC` / `MC.PA` (market `Euronext Paris`) → row shows
  `MC.PA · LVMH Moet Hennessy Louis Vuitton SE · XPAR · EUR€`.
- [ ] Search `ASML` / `ASML.AS` (market `Euronext Amsterdam`) and `UCB` /
  `UCB.BR` (market `Euronext Brussels`) resolve to the right MIC/currency.
- [ ] Market filter `All / NYSE / NASDAQ / SSE / Euronext Paris / Euronext
  Amsterdam / Euronext Brussels` appends
  `?market=XNYS|XNAS|XSHG|XPAR|XAMS|XBRU` (All omits the param).
- [ ] Ambiguous input surfaces candidates; nothing auto-resolves.
- [ ] Security Brief / quote for `MC.PA` formats `€` via `Intl.NumberFormat`,
  shows `MarketStateBadge` from `market_state` with provenance fallback, and
  keeps a `ProvenanceBadge` on every number; no hardcoded live prices.
- [ ] Watchlist target-ccy selector (`USD/EUR/CNY`) + `FXProvenanceBanner`
  (source/as_of/fallback) visible.
- [ ] Watchlist with fresh FX → ranked converted table; with stale/missing FX
  (or `FX_PROVENANCE_MISSING`) → gate message
  `Cross-market comparison unavailable — FX provenance missing` instead of
  ranked numbers; native quotes carry `ProvenanceBadge`; no conversion
  applied while gated.
- [ ] Docs: `docs/API_CONTRACT.md` (M7 appendix), `docs/EURONEXT_NOTES.md`
  (this file).
