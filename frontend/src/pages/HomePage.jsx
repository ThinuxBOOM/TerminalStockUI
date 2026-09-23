import React, { memo, useCallback, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getAuditForecasts, getForecast, getProvidersHealth, getQuote } from "../api/client";
import { getTopSignals } from "../api/signals";
import { getNews } from "../api/news";
import AdSlot from "../components/AdSlot";
import { useAuth } from "../hooks/useAuth";
import StatusPill from "../components/StatusPill";
import MarketLiquidityPanel from "../components/MarketLiquidityPanel";
import MarketIndicesSection, { AspiChart } from "../components/AspiChart";
import LiquidationSection from "../components/LiquidationPanel";
import MarketStatusStrip from "../components/MarketStatusStrip";
import CollapsibleSection from "../components/CollapsibleSection";
import TopSignals from "../components/TopSignals";
import NewsPanel from "../components/NewsPanel";
import { BreadthBar, MarketDetailGraphs } from "../components/MarketGraphs";
import { useMarketDetail, useMarketLiquidity } from "../hooks/useMarketLiquidity";
import useWatchlist from "../hooks/useWatchlist";
import CurrencyValue from "../components/CurrencyValue";
import { changeArrow, changeColor, formatPct1 } from "../utils/format";
import Skeleton from "../components/Skeleton";
import EmptyState from "../components/EmptyState";
import ErrorState from "../components/ErrorState";

function normalizeSymbolInput(v) {
  return v.trim().toUpperCase().replace(/\s+/g, "");
}

function greeting() {
  const h = new Date().getHours();
  if (h < 12) return "Good morning";
  if (h < 17) return "Good afternoon";
  return "Good evening";
}

function formatResearchDate(r) {
  const raw = r?.created_at ?? r?.target_date ?? null;
  if (typeof raw !== "string" || raw.trim() === "") return "—";
  const ms = Date.parse(raw);
  if (!Number.isFinite(ms)) return "—";
  try {
    return new Date(ms).toISOString().slice(0, 10);
  } catch {
    return String(raw).slice(0, 10);
  }
}

function forecastSignal(prob) {
  if (typeof prob !== "number" || !Number.isFinite(prob)) return { word: "NEUTRAL", arrow: "→", cls: "text-term-muted" };
  if (prob >= 0.55) return { word: "RISING", arrow: "↑", cls: "text-term-green" };
  if (prob <= 0.45) return { word: "FALLING", arrow: "↓", cls: "text-term-red" };
  return { word: "NEUTRAL", arrow: "→", cls: "text-term-muted" };
}

const MAX_HOME_WATCHLIST = 10;
const MAX_HOME_REPORTS = 8;

const REGIONS = {
  All: null,
  US: ["XNYS", "XNAS"],
  Europe: ["XPAR", "XAMS", "XBRU"],
  Asia: ["XSHG"],
  "Sri Lanka": ["XCOL"],
};

function compactNum(v) {
  if (v === null || v === undefined || !Number.isFinite(Number(v))) return "—";
  try {
    return new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(Number(v));
  } catch {
    return String(v);
  }
}

// Watchlist preview row: quote + 21D forecast, same query keys as SecurityBrief.
function WatchlistPreviewRow({ symbol, onRemove }) {
  const navigate = useNavigate();
  const q = useQuery({
    queryKey: ["quote", symbol],
    queryFn: ({ signal }) => getQuote(symbol, undefined, { signal }),
    retry: false,
    staleTime: 30000,
  });
  const f = useQuery({
    queryKey: ["forecast", symbol, 21],
    queryFn: ({ signal }) => getForecast(symbol, 21, { signal }),
    retry: false,
    staleTime: 300000,
  });
  const open = useCallback(() => navigate(`/security/${encodeURIComponent(symbol)}`), [navigate, symbol]);
  if (q.isLoading) {
    return (
      <tr className="border-b border-term-border">
        <td colSpan={7} className="p-2"><Skeleton label={`loading ${symbol}…`} lines={1} /></td>
      </tr>
    );
  }
  if (q.isError || !q.data) {
    return (
      <tr className="border-b border-term-border hover:bg-term-panel2">
        <td className="p-2 font-bold text-term-text">{symbol}</td>
        <td colSpan={4} className="p-2 text-xs text-term-muted">unavailable <button type="button" className="ml-2 text-term-green underline" onClick={() => void q.refetch()}>Retry</button></td>
        <td className="p-2"><StatusPill provenance={null} /></td>
        <td className="p-2 text-right"><button type="button" className="term-btn-sm" onClick={() => onRemove(symbol)} aria-label={`Remove ${symbol}`}>✕</button></td>
      </tr>
    );
  }
  const d = q.data;
  const chg = d.change_pct;
  const prob = typeof f.data?.probability === "number" ? f.data.probability : null;
  const sig = forecastSignal(prob);
  return (
    <tr
      className="group cursor-pointer border-b border-term-border transition-colors last:border-0 hover:bg-term-panel2 focus-within:bg-term-panel2"
      tabIndex={0}
      onClick={open}
      onKeyDown={(e) => { if (e.key === "Enter" && e.target === e.currentTarget) open(); }}
      aria-label={`${d.symbol} open security brief`}
    >
      <td className="p-2">
        <Link to={`/security/${encodeURIComponent(d.symbol)}`} onClick={(e) => e.stopPropagation()} className="font-bold text-term-green hover:underline">
          {d.symbol}
        </Link>
      </td>
      <td className="tnum term-num p-2 text-right font-semibold text-term-text">
        <CurrencyValue value={d.price} currency={d.currency ?? "USD"} />
      </td>
      <td className={`tnum term-num p-2 text-right font-semibold ${changeColor(chg)}`}>
        {Number.isFinite(chg) ? `${changeArrow(chg)} ${formatPct1(Math.abs(chg) / 100)}` : "—"}
      </td>
      <td className="tnum term-num p-2 text-right text-term-text">
        {f.isLoading ? "…" : prob === null ? "—" : `${(prob * 100).toFixed(0)}%`}
      </td>
      <td className={`p-2 text-xs font-bold ${sig.cls}`}>
        {sig.arrow}{sig.word}
      </td>
      <td className="p-2"><StatusPill freshness={d.provenance} marketState={d.market_state} provenance={d.provenance} size="sm" /></td>
      <td className="p-2 text-right">
        <span className="inline-flex gap-1 opacity-100 focus-within:opacity-100 md:opacity-0 md:group-hover:opacity-100 md:group-focus-within:opacity-100">
          <Link to={`/security/${encodeURIComponent(d.symbol)}`} onClick={(e) => e.stopPropagation()} className="term-btn-sm" aria-label={`Open ${d.symbol}`}>OPEN</Link>
          <button type="button" className="term-btn-sm" onClick={(e) => { e.stopPropagation(); onRemove(symbol); }} aria-label={`Remove ${symbol}`}>✕</button>
        </span>
      </td>
    </tr>
  );
}
const MemoPreviewRow = memo(WatchlistPreviewRow);

function MarketBoard({ markets, selectedMic, onSelect }) {
  const [region, setRegion] = useState("All");
  const [sortKey, setSortKey] = useState("market");
  const [sortDir, setSortDir] = useState(1);
  const filtered = useMemo(() => {
    const allow = REGIONS[region];
    let rows = allow ? markets.filter((m) => allow.includes(m.mic)) : [...markets];
    const val = (m) => {
      if (sortKey === "change") return m.avg_change_pct ?? -Infinity;
      if (sortKey === "volume") return m.total_volume ?? -1;
      if (sortKey === "turnover") return m.turnover ?? -1;
      return m.mic;
    };
    rows = [...rows].sort((a, b) => {
      const av = val(a); const bv = val(b);
      if (typeof av === "string") return sortDir * String(av).localeCompare(String(bv));
      return sortDir * (Number(av) - Number(bv));
    });
    return rows;
  }, [markets, region, sortKey, sortDir]);
  function toggleSort(k) {
    if (sortKey === k) setSortDir((d) => -d);
    else { setSortKey(k); setSortDir(1); }
  }
  const thBtn = (label, k) => (
    <button type="button" onClick={() => toggleSort(k)} aria-label={`Sort by ${label}`} className="inline-flex items-center gap-1 hover:text-term-text focus-visible:text-term-text">
      {label}<span aria-hidden="true" className="text-[10px]">{sortKey === k ? (sortDir === 1 ? "▲" : "▼") : "⇅"}</span>
    </button>
  );
  return (
    <section className="term-panel min-w-0 p-4" aria-labelledby="market-board" id="market-board">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="market-board-h" className="text-base font-extrabold text-term-text">Market board</h2>
        <div className="flex flex-wrap gap-1" role="group" aria-label="Region filter">
          {Object.keys(REGIONS).map((r) => (
            <button key={r} type="button" onClick={() => setRegion(r)} aria-pressed={region === r} className={region === r ? "term-btn px-2 py-1 text-xs" : "term-btn-ghost px-2 py-1 text-xs"}>
              {r}
            </button>
          ))}
        </div>
      </div>
      <p className="mt-1 text-[11px] text-term-muted">Index · turnover as native price proxy (no FX) · change · volume · status. Click a row for detail.</p>
      {filtered.length === 0 ? (
        <div className="mt-2"><EmptyState title="No markets in this region" detail="Try All regions — Sri Lanka (XCOL) is coming soon." /></div>
      ) : (
        <div className="mt-2 overflow-x-auto">
          <table className="w-full min-w-[640px] text-sm">
            <caption className="sr-only">Market board — index, turnover, change, volume, status</caption>
            <thead className="sticky top-0 z-10 bg-term-panel">
              <tr className="border-b border-term-border text-left text-xs text-term-muted">
                <th scope="col" className="p-2">{thBtn("Index", "market")}</th>
                <th scope="col" className="p-2 text-right">{thBtn("Price¹", "turnover")}</th>
                <th scope="col" className="p-2 text-right">{thBtn("Change", "change")}</th>
                <th scope="col" className="p-2 text-right">{thBtn("Volume", "volume")}</th>
                <th scope="col" className="p-2">Status</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((m) => {
                const chg = m.avg_change_pct;
                const active = m.mic === selectedMic;
                return (
                  <tr
                    key={m.mic}
                    className={`cursor-pointer border-b border-term-border transition-colors last:border-0 hover:bg-term-panel2 focus-within:bg-term-panel2 ${active ? "bg-term-panel2/70" : ""}`}
                    tabIndex={0}
                    onClick={() => onSelect(m.mic)}
                    onKeyDown={(e) => { if ((e.key === "Enter" || e.key === " ") && e.target === e.currentTarget) { e.preventDefault(); onSelect(m.mic); } }}
                    aria-label={`${m.label || m.mic} market detail`}
                  >
                    <td className="p-2">
                      <span className="font-bold text-term-green">{m.label || m.mic}</span>
                      <span className="tnum ml-2 text-[11px] text-term-muted">{m.mic}</span>
                    </td>
                    <td className="tnum term-num p-2 text-right text-term-text" title="Turnover in native currency — no FX conversion">
                      {m.turnover === null || m.turnover === undefined ? "—" : compactNum(m.turnover)}
                      <span className="ml-1 text-[10px] text-term-muted">{m.currency ?? ""}</span>
                    </td>
                    <td className={`tnum term-num p-2 text-right font-semibold ${changeColor(chg)}`}>
                      {chg === null || chg === undefined || !Number.isFinite(chg) ? "—" : `${chg > 0 ? "+" : ""}${chg.toFixed(2)}%`}
                    </td>
                    <td className="tnum term-num p-2 text-right text-term-text">{m.total_volume === null || m.total_volume === undefined ? "—" : compactNum(m.total_volume)}</td>
                    <td className="p-2"><StatusPill provenance={m.provenance} mic={m.mic} /></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <p className="mt-1 text-[10px] text-term-muted">¹ Price = native turnover (price×volume, no FX) — cross-currency totals aren&apos;t comparable.</p>
    </section>
  );
}

function MarketDetail({ mic, market, newsData }) {
  const detail = useMarketDetail(mic, true);
  const rows = detail.data?.rows?.length ? detail.data.rows : (market?.rows ?? []);
  const total = market?.total ?? (market ? market.advancers + market.decliners + market.unchanged : 0);
  if (!market) {
    return (
      <section className="term-panel min-w-0 p-4" aria-labelledby="market-detail-h" id="market-detail">
        <h2 id="market-detail-h" className="text-base font-extrabold text-term-text">Market detail</h2>
        <div className="mt-2"><EmptyState title="Select a market above" detail="Click any snapshot or board row to load its index, breadth, volume, movers and news." /></div>
      </section>
    );
  }
  return (
    <section className="term-panel-hero min-w-0 p-4" aria-labelledby="market-detail-h" id="market-detail">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="term-label">Market detail</p>
          <h2 id="market-detail-h" className="text-lg font-extrabold text-term-text">{market.label || market.mic} <span className="tnum text-xs font-normal text-term-muted">{market.mic} · n={total}</span></h2>
          <p className={`tnum term-num mt-1 text-sm font-bold ${changeColor(market.avg_change_pct)}`}>
            {market.avg_change_pct === null || market.avg_change_pct === undefined ? "—" : `${market.avg_change_pct > 0 ? "+" : ""}${market.avg_change_pct.toFixed(2)}% avg change`}
            <span className="ml-2 font-normal text-term-muted">Vol {market.total_volume == null ? "—" : compactNum(market.total_volume)} · Turnover {market.turnover == null ? "—" : compactNum(market.turnover)} {market.currency ?? ""}</span>
          </p>
        </div>
        <StatusPill provenance={market.provenance} mic={market.mic} size="lg" />
      </div>
      <div className="mt-3 grid min-w-0 gap-4 lg:grid-cols-2">
        <div className="min-w-0">
          <h3 className="term-label">Index chart</h3>
          <div className="mt-2"><AspiChart mic={market.mic} /></div>
        </div>
        <div className="min-w-0">
          <h3 className="term-label">Breadth · volume</h3>
          <div className="mt-2">
            <BreadthBar advancers={market.advancers} decliners={market.decliners} unchanged={market.unchanged} total={total} mic={market.mic} />
          </div>
          <dl className="tnum mt-2 grid grid-cols-2 gap-2 text-xs">
            <div className="term-panel-nested p-2"><dt className="text-term-muted">Turnover (native)</dt><dd className="term-num font-bold">{market.turnover == null ? "—" : compactNum(market.turnover)} {market.currency ?? ""}</dd></div>
            <div className="term-panel-nested p-2"><dt className="text-term-muted">Volume (shares)</dt><dd className="term-num font-bold">{market.total_volume == null ? "—" : compactNum(market.total_volume)}</dd></div>
          </dl>
          <h3 className="term-label mt-3">Top movers</h3>
          <div className="mt-2">
            {detail.isLoading && <Skeleton label={`loading ${market.mic} movers…`} lines={3} variant="table" />}
            {detail.isError && <ErrorState title={`${market.mic} movers unavailable`} detail={detail.error instanceof Error ? detail.error.message : "Per-symbol endpoint unreachable."} onRetry={() => void detail.refetch()} />}
            {!detail.isLoading && !detail.isError && <MarketDetailGraphs rows={rows} />}
          </div>
        </div>
      </div>
      <div className="mt-3">
        <h3 className="term-label">Market news</h3>
        <div className="mt-2">
          <NewsPanel data={newsData} isLoading={false} isError={false} error={null} onRetry={undefined} />
        </div>
      </div>
    </section>
  );
}

function HomePage() {
  const navigate = useNavigate();
  const [signalHorizon, setSignalHorizon] = useState(21);
  const { tier } = useAuth();
  const providers = useQuery({
    queryKey: ["providers-health"],
    queryFn: getProvidersHealth,
    retry: false,
    staleTime: 30000,
  });
  const research = useQuery({
    queryKey: ["audit-forecasts", "recent"],
    queryFn: () => getAuditForecasts(5),
    retry: false,
    staleTime: 60000,
  });
  const signals = useQuery({
    queryKey: ["signals-top", signalHorizon],
    queryFn: ({ signal }) => getTopSignals(signalHorizon, 5, { signal }),
    retry: false,
    staleTime: 60000,
  });
  const news = useQuery({
    queryKey: ["market-news"],
    queryFn: ({ signal }) => getNews("", 20, { signal }),
    retry: false,
    staleTime: 300000,
  });
  const liquidity = useMarketLiquidity();
  const spy = useQuery({
    queryKey: ["quote", "SPY"],
    queryFn: ({ signal }) => getQuote("SPY", undefined, { signal }),
    retry: false,
    staleTime: 30000,
  });
  const { symbols: watchlist, add: addWatchSymbol, remove: removeWatchSymbol } = useWatchlist();
  const [draft, setDraft] = useState("");
  const [heroSearch, setHeroSearch] = useState("");
  const [selectedMic, setSelectedMic] = useState("XNYS");
  function addSymbol() {
    const sym = normalizeSymbolInput(draft);
    if (!sym) return;
    addWatchSymbol(sym, "manual");
    setDraft("");
  }
  const removeSymbol = useCallback((sym) => removeWatchSymbol(sym), [removeWatchSymbol]);
  const showProviders = useMemo(() => providers.data ?? [], [providers.data]);
  const reports = useMemo(() => research.data?.forecasts ?? [], [research.data]);
  const markets = useMemo(() => liquidity.data?.markets ?? [], [liquidity.data]);
  const selectedMarket = useMemo(() => markets.find((m) => m.mic === selectedMic) ?? markets[0] ?? null, [markets, selectedMic]);
  const providerIssueCount = useMemo(
    () => showProviders.filter((p) => p.status !== "ok" || p.circuit === "open").length,
    [showProviders]
  );
  const spyChg = spy.data?.change_pct;

  function heroSubmit(e) {
    e.preventDefault();
    const sym = normalizeSymbolInput(heroSearch);
    if (!sym) return;
    navigate(`/security/${encodeURIComponent(sym)}`);
  }
  function selectMarket(mic) {
    setSelectedMic(mic);
    requestAnimationFrame(() => {
      document.getElementById("market-detail")?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }

  return (
    <div className="grid max-w-full gap-4">
      {/* Compact hero — greeting + MARKET OVERVIEW + S&P, no wasted viewport */}
      <section className="term-panel-hero flex flex-wrap items-center justify-between gap-3 p-4" aria-labelledby="home-hero">
        <div className="min-w-0">
          <p className="text-xs text-term-muted">{greeting()} — <span className="font-semibold text-term-text">MARKET OVERVIEW</span></p>
          <h1 id="home-hero" className="mt-0.5 text-xl font-black text-term-text">
            S&amp;P {spy.data ? (
              <span className={`tnum term-num text-base font-bold ${changeColor(spyChg)}`}>
                <CurrencyValue value={spy.data.price} currency={spy.data.currency ?? "USD"} /> {Number.isFinite(spyChg) ? `${spyChg > 0 ? "+" : ""}${spyChg.toFixed(2)}%` : ""}
              </span>
            ) : spy.isLoading ? <span className="text-sm font-normal text-term-muted">loading…</span> : <span className="text-sm font-normal text-term-muted">—</span>}
          </h1>
        </div>
        <form onSubmit={heroSubmit} className="flex w-full min-w-0 max-w-md flex-1 gap-2 sm:w-auto" role="search" aria-label="Look up a stock">
          <input
            className="term-input min-w-0 flex-1"
            value={heroSearch}
            onChange={(e) => setHeroSearch(e.target.value)}
            placeholder="AAPL, TSLA, 600519.SS…"
            aria-label="Look up a stock by ticker"
            spellCheck={false}
          />
          <button className="term-btn shrink-0" type="submit">GO →</button>
        </form>
      </section>

      <MarketStatusStrip />

      {/* Market snapshot — dense horizontal strip, click → detail */}
      <section className="term-panel min-w-0 p-4" aria-labelledby="snapshot-h">
        <div className="flex items-center justify-between gap-2">
          <h2 id="snapshot-h" className="term-label">Market snapshot — click for detail</h2>
          <span className="tnum text-[11px] text-term-muted">{markets.length} venues</span>
        </div>
        {liquidity.isLoading && <div className="mt-2"><Skeleton label="loading market snapshot…" lines={2} /></div>}
        {liquidity.isError && <div className="mt-2"><ErrorState title="Market snapshot unavailable" detail={liquidity.error instanceof Error ? liquidity.error.message : "Backend /api/markets/overview unreachable."} onRetry={() => void liquidity.refetch()} /></div>}
        {!liquidity.isLoading && !liquidity.isError && markets.length === 0 && (
          <p className="mt-2 text-xs text-term-muted" role="status">No market breadth yet.</p>
        )}
        {!liquidity.isLoading && !liquidity.isError && markets.length > 0 && (
          <div className="mt-2 flex gap-2 overflow-x-auto pb-1" role="list" aria-label="Market snapshot">
            {markets.map((m) => {
              const active = (selectedMarket?.mic ?? selectedMic) === m.mic;
              return (
                <button
                  key={m.mic}
                  type="button"
                  role="listitem"
                  onClick={() => selectMarket(m.mic)}
                  aria-pressed={active}
                  aria-label={`${m.label || m.mic} ${m.avg_change_pct != null ? `${m.avg_change_pct.toFixed(2)} percent` : "unavailable"} — view detail`}
                  className={`min-w-[148px] flex-1 rounded border p-2 text-left transition-colors hover:border-term-green focus-visible:border-term-green ${active ? "border-term-green bg-term-panel2" : "border-term-border bg-term-bg"}`}
                >
                  <span className="block truncate text-xs font-bold text-term-text">{m.mic}</span>
                  <span className={`tnum term-num block text-sm font-bold ${changeColor(m.avg_change_pct)}`}>
                    {m.avg_change_pct == null || !Number.isFinite(m.avg_change_pct) ? "—" : `${m.avg_change_pct > 0 ? "+" : ""}${m.avg_change_pct.toFixed(2)}%`}
                  </span>
                  <span className="mt-1 block"><StatusPill provenance={m.provenance} mic={m.mic} /></span>
                </button>
              );
            })}
          </div>
        )}
      </section>

      {/* Market board table + detail */}
      {liquidity.isLoading ? (
        <Skeleton label="loading market board…" lines={6} variant="table" />
      ) : liquidity.isError ? (
        <ErrorState title="Market board unavailable" detail={liquidity.error instanceof Error ? liquidity.error.message : "Backend unreachable."} onRetry={() => void liquidity.refetch()} />
      ) : (
        <>
          <MarketBoard markets={markets} selectedMic={selectedMarket?.mic ?? selectedMic} onSelect={selectMarket} />
          <MarketDetail mic={selectedMarket?.mic ?? selectedMic} market={selectedMarket} newsData={news.data ?? null} />
        </>
      )}

      {/* Watchlist preview — Symbol/Price/Change/Forecast/Signal/Freshness, max 10 */}
      <section className="term-panel-hero min-w-0 p-4" aria-labelledby="home-watchlist">
        <div className="flex items-center justify-between gap-2">
          <div>
            <h2 id="home-watchlist" className="text-base font-extrabold text-term-text">Watchlist</h2>
            <p className="text-[11px] text-term-muted">Saved in this browser · max {MAX_HOME_WATCHLIST} preview</p>
          </div>
          <Link to="/watchlist" className="term-btn-sm shrink-0">OPEN FULL LIST →</Link>
        </div>
        <form className="mt-2 flex gap-2" onSubmit={(e) => { e.preventDefault(); addSymbol(); }}>
          <input className="term-input min-w-0 flex-1" value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Add ticker (e.g. NVDA)" aria-label="Add symbol to watchlist" spellCheck={false} />
          <button className="term-btn shrink-0" type="submit">+ FOLLOW</button>
        </form>
        {watchlist.length === 0 ? (
          <div className="mt-2"><EmptyState title="Nothing followed yet" detail="Tap + FOLLOW above — e.g. AAPL — and it will appear here with live prices." /></div>
        ) : (
          <div className="mt-2 overflow-x-auto">
            <table className="w-full min-w-[680px] text-sm">
              <caption className="sr-only">Watchlist preview — symbol, price, change, forecast, signal, freshness</caption>
              <thead className="sticky top-0 z-10 bg-term-panel">
                <tr className="border-b border-term-border text-left text-xs text-term-muted">
                  <th scope="col" className="p-2">Symbol</th>
                  <th scope="col" className="p-2 text-right">Price</th>
                  <th scope="col" className="p-2 text-right">Change</th>
                  <th scope="col" className="p-2 text-right">Forecast</th>
                  <th scope="col" className="p-2">Signal</th>
                  <th scope="col" className="p-2">Freshness</th>
                  <th scope="col" className="p-2"><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {watchlist.slice(0, MAX_HOME_WATCHLIST).map((s) => (
                  <MemoPreviewRow key={s} symbol={s} onRemove={removeSymbol} />
                ))}
              </tbody>
            </table>
          </div>
        )}
        {watchlist.length > MAX_HOME_WATCHLIST && (
          <p className="mt-1 text-[11px] text-term-muted" role="status">
            showing first {MAX_HOME_WATCHLIST} of {watchlist.length} — <Link to="/watchlist" className="text-term-green">open full list →</Link>
          </p>
        )}
      </section>

      <TopSignals
        data={signals.data ?? null}
        isLoading={signals.isLoading}
        isError={signals.isError}
        error={signals.error}
        horizon={signalHorizon}
        onHorizon={setSignalHorizon}
        onRetry={() => void signals.refetch()}
      />

      <AdSlot slotId="home-infeed" format="in-feed" tier={tier} slotIndex={2} />

      {/* Research + News split */}
      <div className="grid min-w-0 items-start gap-4 lg:grid-cols-2">
        <section className="term-panel min-w-0 p-4" aria-labelledby="home-research">
          <h2 id="home-research" className="text-base font-extrabold text-term-text">Research</h2>
          <p className="text-[11px] text-term-muted">Recent forecasts the system published.</p>
          {research.isLoading && <div className="mt-2"><Skeleton label="loading latest research…" lines={3} variant="table" /></div>}
          {research.isError && <div className="mt-2"><ErrorState title="Research feed unavailable" detail={research.error instanceof Error ? research.error.message : "Backend /api/audit/forecasts unreachable."} onRetry={() => void research.refetch()} /></div>}
          {!research.isLoading && !research.isError && reports.length === 0 && (
            <p className="mt-2 text-xs text-term-muted" role="status">No reports yet. Forecasts you run will appear here.</p>
          )}
          {!research.isLoading && !research.isError && reports.length > 0 && (
            <div className="mt-2 overflow-x-auto">
              <table className="w-full min-w-[420px] text-xs">
                <caption className="sr-only">Latest research forecasts</caption>
                <thead className="sticky top-0 z-10 bg-term-panel">
                  <tr className="border-b border-term-border text-left text-term-muted">
                    <th scope="col" className="p-2">Symbol</th>
                    <th scope="col" className="p-2">Horizon</th>
                    <th scope="col" className="p-2 text-right">Prob</th>
                    <th scope="col" className="p-2 text-right">Date</th>
                  </tr>
                </thead>
                <tbody>
                  {reports.slice(0, MAX_HOME_REPORTS).map((r, i) => (
                    <tr key={r.forecast_id ?? `${r.symbol ?? "unknown"}-${i}`} className="border-b border-term-border transition-colors last:border-0 hover:bg-term-panel2">
                      <td className="p-2">
                        {r.symbol ? <Link to={`/security/${encodeURIComponent(r.symbol)}`} className="font-bold text-term-green hover:underline">{r.symbol}</Link> : <span className="text-term-muted">—</span>}
                      </td>
                      <td className="tnum p-2 text-term-muted">{r.horizon_days ? `${r.horizon_days}d` : "—"}</td>
                      <td className="tnum term-num p-2 text-right text-term-text">{formatPct1(r.direction_probability)}</td>
                      <td className="tnum p-2 text-right text-term-muted">{formatResearchDate(r)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
        <section className="term-panel min-w-0 p-4" aria-labelledby="home-news">
          <div className="flex items-center justify-between gap-2">
            <div>
              <h2 id="home-news" className="text-base font-extrabold text-term-text">Market news</h2>
              <p className="text-[11px] text-term-muted">What is moving US stocks right now.</p>
            </div>
            <button className="term-btn-sm shrink-0" type="button" onClick={() => void news.refetch()}>REFRESH</button>
          </div>
          <div className="mt-2">
            <NewsPanel data={news.data ?? null} isLoading={news.isLoading} isError={news.isError} error={news.error} onRetry={() => void news.refetch()} />
          </div>
        </section>
      </div>

      <CollapsibleSection id="home-liquidity" title="💧 Market activity (liquidity)" subtitle="How busy is each market? Busy usually means easier to buy/sell." defaultOpen={false}>
        <MarketLiquidityPanel data={liquidity.data ?? null} isLoading={liquidity.isLoading} isError={liquidity.isError} error={liquidity.error} onRetry={() => void liquidity.refetch()} />
        <div className="mt-4"><LiquidationSection /></div>
      </CollapsibleSection>

      <CollapsibleSection id="home-aspi" title="📈 Market indexes (ASPI & friends)" subtitle="One line per market — click SHOW to expand." defaultOpen={false}>
        <MarketIndicesSection />
      </CollapsibleSection>

      <CollapsibleSection
        id="home-providers"
        title="🔌 Data health"
        subtitle="Where do our numbers come from, and is each source working?"
        defaultOpen={false}
        badge={providers.data ? (providerIssueCount === 0 ? <span className="term-btn-sm">● ALL HEALTHY</span> : <span className="term-btn-sm">● {providerIssueCount} ISSUE{providerIssueCount === 1 ? "" : "S"}</span>) : null}
      >
        {providers.isLoading && <div className="mt-2"><Skeleton label="loading provider health…" lines={3} variant="table" /></div>}
        {providers.isError && <div className="mt-2"><ErrorState title="Data health unavailable" detail={providers.error instanceof Error ? providers.error.message : "Backend /api/providers/health unreachable."} onRetry={() => void providers.refetch()} /></div>}
        {!providers.isLoading && !providers.isError && showProviders.length === 0 && (
          <p className="mt-2 text-xs text-term-muted" role="status">No provider data — the health endpoint returned no rows.</p>
        )}
        {showProviders.length > 0 && (
          <div className="mt-2 overflow-x-auto">
            <table className="w-full min-w-[480px] text-xs">
              <caption className="sr-only">Data provider health</caption>
              <thead className="sticky top-0 z-10 bg-term-panel">
                <tr className="border-b border-term-border text-left text-term-muted">
                  <th scope="col" className="p-2">Source</th>
                  <th scope="col" className="p-2">Health</th>
                  <th scope="col" className="p-2 text-right">Latency</th>
                </tr>
              </thead>
              <tbody>
                {showProviders.map((p) => {
                  const latency = p.latency_p50_ms ?? p.latency_ms ?? p.latency_p95_ms;
                  const bad = p.status !== "ok" || (p.circuit !== undefined && p.circuit === "open");
                  return (
                    <tr key={p.name} className="border-b border-term-border transition-colors last:border-0 hover:bg-term-panel2">
                      <td className="p-2">{p.name}</td>
                      <td className={`tnum p-2 font-semibold ${bad ? "text-term-red" : "text-term-green"}`}>{bad ? "● needs attention" : "● working"}{p.circuit ? ` · ${p.circuit}` : ""}</td>
                      <td className="tnum term-num p-2 text-right text-term-muted">{latency !== undefined && latency !== null ? `${latency}ms` : "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </CollapsibleSection>
    </div>
  );
}
export { HomePage as default };
