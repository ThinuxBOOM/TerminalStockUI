# SSE notes (M6) — Shanghai Stock Exchange via Yahoo `.SS` + AKShare fallback

Seed symbols (see `backend/instruments/registry.py`):

- `600519.SS` — Kweichow Moutai Co., Ltd. (`XSHG`, `CNY`, `CNE0000018R8`)
- `600000.SS` — Shanghai Pudong Development Bank (`XSHG`, `CNY`, `CNE000000JP3`)

## Yahoo `.SS` suffix

- Canonical provider (Yahoo-style) symbol for XSHG is `<base>.SS` (see `backend/instruments/calendars.py`: `suffix_for_mic("XSHG") == ".SS"`, `provider_symbol_for`).
- Identity is `(exchange_mic, exchange_symbol)` — never the bare ticker. `600519` and `600519.SS` resolve to the same instrument; the UI always displays the provider form `600519.SS`.
- Search (`GET /api/instruments/search`) matches `exchange_symbol`, `provider_symbol`, and `company_name` (so `Moutai` works); `?market=XSHG` scopes to SSE.
- Quote (`GET /api/market_data/quote?symbol=600519.SS&market=XSHG`) returns the canonical `instrument` block + `currency: CNY` + `market_state` + `ambiguous/candidates` (surfaced, never guessed).

## AKShare fallback

- Primary free source for SSE is yfinance (Yahoo `.SS`); AKShare is the documented fallback/secondary (spec §4 table).
- Every quote still carries the full `provenance` envelope (`source` names the provider that actually served the data, `fallback_used: true` when served from cache/secondary).
- Frontend fallback rule: missing `market_state` → derive `open | delayed | stale` from provenance only; `closed` / `lunch` are never synthesized client-side.

## T+1 / price-limit / lunch assumptions (v1)

- **T+1 settlement:** SSE is T+1 (same-day sell restricted). v1 is read-only analysis — no order path — so this affects interpretation of volume/event timelines only, not execution.
- **Price limits:** ~±10% daily limit on most SSE names (±5% ST/*ST). Deterministic analytics treat limit-locked bars as observed data (flagged via `missing_fields` / quality notes when sparse); no synthetic un-limiting.
- **Lunch break:** 11:30–13:00 Asia/Shanghai maps to `market_state: lunch`. The badge renders `LUNCH BREAK` (amber); freshness/provenance badges stay alongside it.
- **Currency:** all SSE money is `CNY`, formatted with `Intl.NumberFormat('zh-CN', { style: 'currency', currency: 'CNY' })` → `¥`. No FX conversion in v1 (M0 gate).

## Holiday stub limits

- Exchange-holiday calendars are stubs in v1: `market_state` outside scheduled sessions may report `delayed`/`stale` from provenance age rather than a calendar-exact `closed`.
- Do not treat `open` as a trading signal; it means "fresh within expected delay". Calendar-exact open/closed comes only when the backend sends it explicitly.

## Acceptance checklist (M6 search/currency)

- [ ] Search `Moutai` (or `600519` / `600519.SS` with market `SSE`) → row shows `Kweichow Moutai Co., Ltd. · XSHG · CNY¥ · 600519.SS`.
- [ ] Market filter `All / NYSE / NASDAQ / SSE` appends `?market=XNYS|XNAS|XSHG` (All omits the param).
- [ ] Ambiguous input surfaces candidates (`Ambiguous — N candidates…`, quote `ambiguous + candidates[]`); nothing auto-resolves.
- [ ] Security Brief for `600519.SS` formats price via `Intl.NumberFormat` (`¥`), shows `MarketStateBadge` (`OPEN/CLOSED/LUNCH/DELAYED/STALE`) from `market_state` with provenance fallback, and keeps a `ProvenanceBadge` on every number.
- [ ] No hardcoded live prices; missing price renders `unavailable`.
