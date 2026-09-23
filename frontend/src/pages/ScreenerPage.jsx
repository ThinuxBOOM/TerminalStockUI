import React, { useEffect, useMemo, useState } from "react";
import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { FORECAST_HORIZONS, getScreener } from "../api/client";
import AdSlot from "../components/AdSlot";
import { useAuth } from "../hooks/useAuth";
import CurrencyValue from "../components/CurrencyValue";
import EmptyState from "../components/EmptyState";
import ErrorState from "../components/ErrorState";
import MarketStateBadge from "../components/MarketStateBadge";
import ProvenanceBadge from "../components/ProvenanceBadge";
import Skeleton from "../components/Skeleton";
import StatusPill from "../components/StatusPill";
import { changeArrow, changeColor } from "../utils/format";

const MARKET_OPTIONS = [
  { label: "All", value: "" },
  { label: "S&P 500 (US index)", value: "SP500" },
  { label: "NYSE", value: "XNYS" },
  { label: "NASDAQ", value: "XNAS" },
  { label: "SSE", value: "XSHG" },
  { label: "Euronext Paris", value: "XPAR" },
  { label: "Euronext Amsterdam", value: "XAMS" },
  { label: "Euronext Brussels", value: "XBRU" }
];
function clampProb(v) {
  if (!Number.isFinite(v)) return 0.5;
  return Math.min(1, Math.max(0, v));
}
function qualityInfo(r) {
  const q = r.quality ?? {};
  const flag = typeof q.quality_flag === "string" && q.quality_flag ? q.quality_flag : "—";
  const reason = typeof q.reason === "string" ? q.reason : "";
  return { flag, reason };
}
function qualityFlag(r) {
  return qualityInfo(r).flag;
}
function qualityReason(r) {
  return qualityInfo(r).reason || "Quality signal unavailable for this row";
}
const MAX_SKIPPED_SHOWN = 10;
function screenerErrorDetail(error) {
  const message = error instanceof Error ? error.message : "Backend unreachable. Check VITE_API_BASE_URL.";
  if (error?.code === "ECONNABORTED" || /timeout of \d+ms exceeded/i.test(message)) {
    return "Scan timed out cold (large universes fetch many live quotes). Retry — warm quotes/cache make repeats faster; partial scans list the rest under skipped.";
  }
  return message;
}
function ScreenerPage() {
  const [params] = useSearchParams();
  const urlMarket = (params.get("market") ?? "").trim().toUpperCase();
  const urlQ = params.get("q") ?? "";
  const [market, setMarket] = useState(urlMarket);
  const [horizon, setHorizon] = useState(21);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [symbolFilter, setSymbolFilter] = useState(urlQ);
  const { tier } = useAuth();
  const SCREENER_LIMIT = 20;
  const [offset, setOffset] = useState(0);
  const [minProbInput, setMinProbInput] = useState(0.5);
  const [minProb, setMinProb] = useState(0.5);
  useEffect(() => {
    const t = setTimeout(() => setMinProb(clampProb(minProbInput)), 450);
    return () => clearTimeout(t);
  }, [minProbInput]);
  useEffect(() => {
    setOffset(0);
  }, [market, horizon, minProb]);
  const mic = market.trim().toUpperCase();
  const screen = useQuery({
    queryKey: ["screener", mic || "ALL", horizon, minProb, SCREENER_LIMIT, offset],
    queryFn: ({ signal }) => getScreener({
      market: mic || void 0,
      minDirection: minProb,
      horizon,
      limit: SCREENER_LIMIT,
      offset
    }, { signal }),
    staleTime: 6e4,
    gcTime: 3e5,
    retry: false,
    placeholderData: keepPreviousData
  });
  const data = screen.data;
  const allRows = useMemo(() => data?.results ?? [], [data]);
  const rows = useMemo(() => {
    const ft = symbolFilter.trim().toUpperCase();
    if (!ft) return allRows;
    return allRows.filter((r) => String(r.symbol ?? "").toUpperCase().includes(ft) || String(r.company_name ?? "").toUpperCase().includes(ft));
  }, [allRows, symbolFilter]);
  const skippedCount = data?.skipped?.length ?? 0;
  const skippedSymbols = useMemo(
    () => (data?.skipped ?? []).slice(0, MAX_SKIPPED_SHOWN).map((s) => s.symbol),
    [data]
  );
  const skippedOverflow = skippedCount > skippedSymbols.length;
  const filters = (
    <div className="space-y-4">
      <div>
        <label className="term-label" htmlFor="screener-market">Market</label>
        <select id="screener-market" className="term-input mt-1 w-full" value={market} onChange={(e) => setMarket(e.target.value)} aria-label="Filter screener by market">
          {MARKET_OPTIONS.map((m) => <option key={m.label} value={m.value}>{m.label}</option>)}
        </select>
      </div>
      <div>
        <p className="term-label" id="screener-horizon-label">Horizon (trading days)</p>
        <div className="mt-1 flex flex-wrap gap-1" role="group" aria-labelledby="screener-horizon-label">
          {FORECAST_HORIZONS.map((h) => (
            <button key={h} type="button" className={h === horizon ? "term-btn" : "term-btn-ghost"} aria-pressed={h === horizon} onClick={() => setHorizon(h)}>{h}d</button>
          ))}
        </div>
      </div>
      <div>
        <label className="term-label" htmlFor="screener-min-prob">Min probability {(minProbInput * 100).toFixed(0)}%</label>
        <div className="mt-1 flex items-center gap-2">
          <input id="screener-min-prob" type="range" min={0} max={1} step={0.01} value={minProbInput} onChange={(e) => setMinProbInput(clampProb(Number(e.target.value)))} aria-label="Minimum direction probability (slider)" className="w-full min-w-0 flex-1" />
          <input type="number" min={0} max={1} step={0.01} value={minProbInput} onChange={(e) => setMinProbInput(clampProb(Number(e.target.value)))} aria-label="Minimum direction probability (numeric)" className="term-input w-20 shrink-0" />
        </div>
      </div>
      <div>
        <label className="term-label" htmlFor="screener-q">Search results</label>
        <input id="screener-q" className="term-input mt-1 w-full" value={symbolFilter} onChange={(e) => setSymbolFilter(e.target.value)} placeholder="Filter symbol / company…" aria-label="Filter screener results by symbol" spellCheck={false} />
      </div>
      <p className="text-[11px] text-term-muted">Deterministic ensemble at {horizon}d · ranked by direction prob desc · offset capped at 200.</p>
      <button type="button" className="term-btn-ghost w-full text-xs" onClick={() => { setMarket(""); setHorizon(21); setMinProbInput(0.5); setMinProb(0.5); setOffset(0); setSymbolFilter(""); }}>RESET FILTERS</button>
    </div>
  );
  return (
    <div className="max-w-full">
      <nav className="mb-3 text-xs" aria-label="Breadcrumb"><Link to="/" className="text-term-muted hover:text-term-text">← Home</Link></nav>
      <h1 className="text-lg font-extrabold text-term-text">Discover — Screener</h1>
      <p className="mt-0.5 text-xs text-term-muted">Search + filters + ranked results. Forecasts are research starting points, never guarantees.</p>
      <div className="mt-3 grid min-w-0 items-start gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
        {/* Filters drawer on mobile, side panel on desktop */}
        <div className="lg:hidden">
          <button type="button" className="term-btn-ghost w-full text-xs" onClick={() => setFiltersOpen((v) => !v)} aria-expanded={filtersOpen} aria-controls="screener-filters">
            {filtersOpen ? "▾ HIDE FILTERS" : "▸ SHOW FILTERS (Market · Horizon · Prob)"}
          </button>
          {filtersOpen && <section id="screener-filters" className="term-panel mt-2 p-4" aria-label="Screener filters">{filters}</section>}
        </div>
        <aside className="term-panel hidden min-w-0 p-4 lg:block" aria-label="Screener filters">
          <h2 className="term-label">Filters</h2>
          <div className="mt-2">{filters}</div>
        </aside>
        <div className="min-w-0">
          <AdSlot slotId="screener-below-filters" format="in-feed" tier={tier} slotIndex={2} />
          <div className="mt-2">
            {screen.isLoading && <Skeleton label="scanning universe…" lines={6} variant="table" />}
            {screen.isError && <ErrorState title="Screener unavailable" detail={screenerErrorDetail(screen.error)} onRetry={() => void screen.refetch()} />}
            {!screen.isLoading && !screen.isError && data && (
              <div className="space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="text-xs text-term-muted" role="status">{data.count} of {data.filtered_total ?? data.universe_size} pass · page {Math.floor((data.offset ?? offset) / SCREENER_LIMIT) + 1}{(data.offset ?? offset) >= 200 ? " · offset capped at 200 — refine filters" : ""}{skippedCount > 0 && ` · ${skippedCount} skipped`}{symbolFilter ? ` · filter “${symbolFilter}”` : ""}{screen.isFetching ? " · refreshing…" : ""}</p>
                  <div className="flex items-center gap-2">
                    <button className="term-btn-ghost text-xs" type="button" disabled={(data.offset ?? offset) <= 0 || screen.isFetching} onClick={() => setOffset((o) => Math.max(0, o - SCREENER_LIMIT))} aria-label="Previous screener page">← PREV</button>
                    <button className="term-btn-ghost text-xs" type="button" disabled={(data.offset ?? offset) + data.count >= (data.filtered_total ?? data.count) || screen.isFetching || (data.offset ?? offset) >= 200} onClick={() => setOffset((o) => Math.min(200, o + SCREENER_LIMIT))} title={(data.offset ?? offset) >= 200 ? "Offset capped at 200 by the backend — refine filters to narrow beyond 220 rows." : void 0} aria-label="Next screener page">NEXT →</button>
                  </div>
                </div>
                {rows.length === 0 ? (
                  <EmptyState title="No instruments pass the screen" detail="Lower the minimum probability or widen the market filter." actionLabel="Reset filters" onAction={() => { setMarket(""); setHorizon(21); setMinProbInput(0.5); setMinProb(0.5); setOffset(0); setSymbolFilter(""); }} />
                ) : (
                  <div className="term-panel-hero overflow-x-auto">
                    <table className="w-full min-w-[720px] text-sm">
                      <caption className="sr-only">Screener results ranked by forecast direction</caption>
                      <thead className="sticky top-0 z-10 bg-term-panel">
                        <tr className="border-b border-term-border text-left text-xs text-term-muted">
                          <th scope="col" className="p-2">#</th>
                          <th scope="col" className="p-2">Symbol</th>
                          <th scope="col" className="p-2 text-right">Price</th>
                          <th scope="col" className="p-2 text-right">Change</th>
                          <th scope="col" className="p-2 text-right">Prob {horizon}d</th>
                          <th scope="col" className="p-2">Confidence</th>
                          <th scope="col" className="p-2">Quality</th>
                          <th scope="col" className="p-2">Market</th>
                          <th scope="col" className="p-2">Freshness</th>
                        </tr>
                      </thead>
                      <tbody>
                        {rows.map((r, idx) => {
                          const chg = r.change_pct;
                          return (
                            <tr key={`${r.symbol}-${r.exchange_mic}-${idx}`} className="border-b border-term-border transition-colors last:border-0 hover:bg-term-panel2 even:bg-term-panel2/40">
                              <td className="tnum p-2 text-term-muted">{(data.offset ?? offset) + idx + 1}</td>
                              <td className="p-2">
                                <Link to={`/security/${encodeURIComponent(r.symbol)}`} className="font-bold text-term-green hover:underline focus-visible:underline">{r.symbol}</Link>
                                <span className="ml-2 hidden text-xs text-term-muted xl:inline">{r.company_name}{r.exchange_mic ? ` · ${r.exchange_mic}` : ""}</span>
                              </td>
                              <td className="tnum term-num p-2 text-right"><CurrencyValue value={r.price} currency={r.currency} /></td>
                              <td className={`tnum term-num p-2 text-right font-semibold ${changeColor(chg)}`}>{typeof chg === "number" && Number.isFinite(chg) ? `${changeArrow(chg)} ${Math.abs(chg).toFixed(2)}%` : "—"}</td>
                              <td className="tnum term-num p-2 text-right"><b>{Number.isFinite(r.direction_probability) ? `${(r.direction_probability * 100).toFixed(1)}%` : "—"}</b></td>
                              <td className="p-2 text-xs">{r.confidence}</td>
                              <td className="p-2 text-xs text-term-muted" title={qualityReason(r)}>{qualityFlag(r)}</td>
                              <td className="p-2"><MarketStateBadge state={r.market_state} provenance={r.provenance} /></td>
                              <td className="p-2"><span className="inline-flex items-center gap-1"><ProvenanceBadge p={r.provenance} /><StatusPill provenance={r.provenance} /></span></td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                    <p className="p-2 text-[11px] text-term-muted">{data.disclosure || "Not investment advice."}{skippedCount > 0 && <span className="ml-2">Skipped: {skippedSymbols.join(", ")}{skippedOverflow ? ` +${skippedCount - skippedSymbols.length} more` : ""}.</span>}</p>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
export { ScreenerPage as default };
