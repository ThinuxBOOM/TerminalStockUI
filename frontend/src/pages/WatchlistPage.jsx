import React, { useCallback, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import useWatchlist from "../hooks/useWatchlist";
import { TARGET_CURRENCIES, extractFxGateProvenance, getForecast, getQuote, isFreshFxProvenance, normalizeTargetCcy, rankCrossMarket } from "../api/client";
import CurrencyValue from "../components/CurrencyValue";
import ErrorState from "../components/ErrorState";
import EmptyState from "../components/EmptyState";
import Skeleton from "../components/Skeleton";
import StatusPill from "../components/StatusPill";
import FXProvenanceBanner from "../components/FXProvenanceBanner";
import { changeArrow, changeColor, formatPct1, formatDateTime } from "../utils/format";

const GATE_MESSAGE = "Cross-market comparison unavailable — FX provenance missing";
const MAX_WATCHLIST_ROWS = 100;

function normalizeSymbolInput(v) {
  return v.trim().toUpperCase().replace(/\s+/g, "");
}

async function fetchNativeQuotes(symbols, signal) {
  const settled = await Promise.all(
    symbols.map(async (s) => {
      try {
        return await getQuote(s, void 0, { signal });
      } catch (err) {
        return {
          symbol: s,
          price: null,
          currency: null,
          market_state: null,
          instrument: null,
          provenance: null,
          _error: err instanceof Error ? err.message : String(err ?? "quote failed"),
        };
      }
    })
  );
  return settled;
}

function forecastSignal(prob) {
  if (typeof prob !== "number" || !Number.isFinite(prob)) return { word: "NEUTRAL", arrow: "→", cls: "text-term-muted" };
  if (prob >= 0.55) return { word: "RISING", arrow: "↑", cls: "text-term-green" };
  if (prob <= 0.45) return { word: "FALLING", arrow: "↓", cls: "text-term-red" };
  return { word: "NEUTRAL", arrow: "→", cls: "text-term-muted" };
}

function ForecastCells({ symbol }) {
  const f = useQuery({
    queryKey: ["forecast", symbol, 21],
    queryFn: ({ signal }) => getForecast(symbol, 21, { signal }),
    retry: false,
    staleTime: 300000,
  });
  const prob = typeof f.data?.probability === "number" ? f.data.probability : null;
  const sig = forecastSignal(prob);
  if (f.isLoading) {
    return (
      <>
        <td className="tnum term-num p-2 text-right text-term-muted">…</td>
        <td className="p-2 text-xs text-term-muted">…</td>
      </>
    );
  }
  return (
    <>
      <td className="tnum term-num p-2 text-right font-semibold text-term-text">
        {prob === null ? "—" : `${(prob * 100).toFixed(0)}%`}
      </td>
      <td className={`p-2 text-xs font-bold ${sig.cls}`} title={f.data ? `21D forecast ${prob !== null ? `${(prob * 100).toFixed(0)}%` : ""}` : "Forecast unavailable"}>
        {prob === null ? "—" : `${sig.arrow}${sig.word}`}
      </td>
    </>
  );
}

function WatchlistPage() {
  const navigate = useNavigate();
  const { symbols, add, remove, clear } = useWatchlist();
  const [draft, setDraft] = useState("");
  const [targetCcy, setTargetCcy] = useState("USD");
  const [filterText, setFilterText] = useState("");
  const [sortKey, setSortKey] = useState("symbol");
  const [sortDir, setSortDir] = useState(1);
  const sortedSymbols = useMemo(() => [...symbols].sort(), [symbols]);
  const symbolsKey = sortedSymbols.join(",");
  const quotesQuery = useQuery({
    queryKey: ["watchlist", "quotes", symbolsKey],
    queryFn: ({ signal }) => fetchNativeQuotes(sortedSymbols, signal),
    enabled: sortedSymbols.length > 0,
    staleTime: 3e4,
    gcTime: 3e5,
    placeholderData: keepPreviousData,
    retry: false
  });
  const rankQuery = useQuery({
    queryKey: ["watchlist", "rank", symbolsKey, targetCcy],
    queryFn: ({ signal }) => rankCrossMarket(sortedSymbols, targetCcy, { signal }),
    enabled: sortedSymbols.length > 0,
    staleTime: 3e4,
    gcTime: 3e5,
    placeholderData: keepPreviousData,
    retry: false
  });
  const errGateProvenance = extractFxGateProvenance(rankQuery.error);
  const fxProvenance = rankQuery.data?.fx_provenance ?? errGateProvenance ?? null;
  const fxFresh = isFreshFxProvenance(fxProvenance);
  const rankLoading = rankQuery.isLoading && !rankQuery.data;
  const gated = !rankLoading && (rankQuery.isError || !rankQuery.data || !fxFresh);

  const quoteBySymbol = useMemo(() => {
    const m = new Map();
    (quotesQuery.data ?? []).forEach((q, i) => {
      const sym = sortedSymbols[i] ?? q?.symbol;
      if (sym) m.set(String(sym).toUpperCase(), q);
    });
    return m;
  }, [quotesQuery.data, sortedSymbols]);

  const rankBySymbol = useMemo(() => {
    const m = new Map();
    (rankQuery.data?.ranking ?? []).forEach((r) => {
      if (r?.symbol) m.set(String(r.symbol).toUpperCase(), r);
    });
    return m;
  }, [rankQuery.data]);

  const displaySymbols = useMemo(() => {
    let list = [...sortedSymbols].slice(0, MAX_WATCHLIST_ROWS);
    const ft = filterText.trim().toUpperCase();
    if (ft) {
      list = list.filter((s) => {
        const q = quoteBySymbol.get(String(s).toUpperCase());
        const name = String(q?.instrument?.company_name ?? "").toUpperCase();
        return String(s).toUpperCase().includes(ft) || name.includes(ft);
      });
    }
    const priceOf = (s) => {
      const key = String(s).toUpperCase();
      if (!gated) {
        const r = rankBySymbol.get(key);
        return r?.converted_price ?? r?.price ?? null;
      }
      return quoteBySymbol.get(key)?.price ?? null;
    };
    const changeOf = (s) => {
      const key = String(s).toUpperCase();
      if (!gated) {
        const r = rankBySymbol.get(key);
        if (r && typeof r.change_pct === "number") return r.change_pct;
      }
      return quoteBySymbol.get(key)?.change_pct ?? null;
    };
    list = [...list].sort((a, b) => {
      if (sortKey === "price") {
        const av = priceOf(a); const bv = priceOf(b);
        if (av == null && bv == null) return 0;
        if (av == null) return 1;
        if (bv == null) return -1;
        return sortDir * (av - bv);
      }
      if (sortKey === "change") {
        const av = changeOf(a) ?? -Infinity; const bv = changeOf(b) ?? -Infinity;
        return sortDir * (av - bv);
      }
      return sortDir * String(a).localeCompare(String(b));
    });
    return list;
  }, [sortedSymbols, filterText, sortKey, sortDir, gated, quoteBySymbol, rankBySymbol]);

  const lastUpdate = useMemo(() => {
    const ts = Math.max(quotesQuery.dataUpdatedAt ?? 0, rankQuery.dataUpdatedAt ?? 0);
    return ts > 0 ? formatDateTime(new Date(ts).toISOString()) : null;
  }, [quotesQuery.dataUpdatedAt, rankQuery.dataUpdatedAt]);

  function addSymbol() {
    const sym = normalizeSymbolInput(draft);
    if (!sym) return;
    add(sym, "manual");
    setDraft("");
  }
  const removeSymbol = useCallback((sym) => remove(sym), [remove]);
  const clearWatchlist = useCallback(() => clear(), [clear]);
  function toggleSort(k) {
    if (sortKey === k) setSortDir((d) => -d);
    else { setSortKey(k); setSortDir(1); }
  }
  function openSecurity(sym) {
    navigate(`/security/${encodeURIComponent(sym)}`);
  }

  if (symbols.length === 0) {
    return (
      <div className="max-w-full">
        <nav className="mb-3 text-xs" aria-label="Breadcrumb"><Link to="/" className="text-term-muted hover:text-term-text">← Home</Link></nav>
        <h1 className="mb-1 text-lg font-extrabold text-term-text">Watchlist</h1>
        <p className="mb-3 text-xs text-term-muted">Monitoring workspace — track prices, 21D forecasts and freshness in one table.</p>
        <EmptyState
          title="No watchlist symbols yet..."
          detail="Follow your first security to start monitoring — prices, forecasts and freshness will appear here."
          actionLabel="Add Security"
          onAction={() => navigate("/search")}
        />
        <form className="mt-3 flex max-w-md gap-2" onSubmit={(e) => { e.preventDefault(); addSymbol(); }}>
          <input className="term-input min-w-0 flex-1" value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Add symbol (e.g. AAPL)" aria-label="Add symbol to watchlist" spellCheck={false} />
          <button className="term-btn" type="submit">ADD</button>
        </form>
      </div>
    );
  }

  return (
    <div className="max-w-full">
      <nav className="mb-3 text-xs" aria-label="Breadcrumb"><Link to="/" className="text-term-muted hover:text-term-text">← Home</Link></nav>
      <h1 className="text-lg font-extrabold text-term-text">Watchlist</h1>
      <p className="mt-0.5 text-xs text-term-muted">Monitoring workspace — search, sort, open research. {gated ? "Native prices (FX gated)." : `Ranked in ${rankQuery.data?.target_ccy ?? targetCcy}.`}</p>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <label htmlFor="target-ccy" className="text-xs text-term-muted">Target currency</label>
        <select id="target-ccy" className="term-input" value={targetCcy} onChange={(e) => setTargetCcy(normalizeTargetCcy(e.target.value))} aria-label="Target currency for cross-market comparison">
          {TARGET_CURRENCIES.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <input className="term-input min-w-0 flex-1 sm:max-w-[220px]" value={filterText} onChange={(e) => setFilterText(e.target.value)} placeholder="Filter by symbol or company…" aria-label="Filter watchlist" spellCheck={false} />
        <div className="flex gap-1" role="group" aria-label="Sort watchlist">
          {[["symbol", "Symbol"], ["price", "Price"], ["change", "Change"]].map(([k, label]) => (
            <button key={k} type="button" onClick={() => toggleSort(k)} aria-pressed={sortKey === k} className={sortKey === k ? "term-btn px-2 py-1 text-xs" : "term-btn-ghost px-2 py-1 text-xs"}>
              {label}{sortKey === k ? (sortDir === 1 ? " ▲" : " ▼") : ""}
            </button>
          ))}
        </div>
        <form className="flex min-w-0 flex-1 gap-2 sm:max-w-[280px]" onSubmit={(e) => { e.preventDefault(); addSymbol(); }}>
          <input className="term-input min-w-0 flex-1" value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Add symbol (e.g. MC.PA)" aria-label="Add symbol to watchlist" spellCheck={false} />
          <button className="term-btn shrink-0" type="submit">ADD</button>
        </form>
        <button className="term-btn-ghost text-xs" type="button" onClick={clearWatchlist} aria-label="Clear watchlist">CLEAR</button>
      </div>

      <div className="mt-3">
        <FXProvenanceBanner provenance={fxProvenance} targetCcy={targetCcy} loading={rankQuery.isLoading} error={rankQuery.error} />
      </div>

      {gated && (
        <div className="term-panel mb-2 border-term-red/40 p-3 text-sm" role="alert">
          <p className="font-bold text-term-red">{GATE_MESSAGE}</p>
          <p className="mt-1 text-xs text-term-muted">Showing native-currency quotes only; no conversion applied. {rankQuery.error ? (rankQuery.error instanceof Error ? rankQuery.error.message : "FX rank endpoint unreachable.") : ""}</p>
        </div>
      )}

      {(quotesQuery.isLoading && !quotesQuery.data) || (rankLoading) ? (
        <Skeleton label="loading watchlist…" lines={6} variant="table" />
      ) : quotesQuery.isError && (!quotesQuery.data || quotesQuery.data.length === 0) ? (
        <ErrorState
          title="Watchlist quotes unavailable"
          detail={`${quotesQuery.error instanceof Error ? quotesQuery.error.message : "Backend unreachable."}${lastUpdate ? ` Last successful update: ${lastUpdate}.` : ""}`}
          onRetry={() => { void quotesQuery.refetch(); void rankQuery.refetch(); }}
        />
      ) : (
        <div className="term-panel-hero overflow-x-auto">
          <table className="w-full min-w-[760px] text-sm">
            <caption className="sr-only">Watchlist — symbol, price, change, forecast, signal, freshness</caption>
            <thead className="sticky top-0 z-10 bg-term-panel">
              <tr className="border-b border-term-border text-left text-xs text-term-muted">
                <th scope="col" className="p-2">Symbol</th>
                <th scope="col" className="p-2 text-right">Price{gated ? "" : ` (${rankQuery.data?.target_ccy ?? targetCcy})`}</th>
                <th scope="col" className="p-2 text-right">Change</th>
                <th scope="col" className="p-2 text-right">Forecast 21D</th>
                <th scope="col" className="p-2">Signal</th>
                <th scope="col" className="p-2">Freshness</th>
                <th scope="col" className="p-2"><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {displaySymbols.map((sym) => {
                const key = String(sym).toUpperCase();
                const q = quoteBySymbol.get(key);
                const r = rankBySymbol.get(key);
                const errRow = q?._error || q?.price == null;
                const price = !gated && r ? (r.converted_price ?? r.price) : q?.price;
                const ccy = !gated && r ? (rankQuery.data?.target_ccy ?? targetCcy) : (q?.currency ?? null);
                const chg = (!gated && r && typeof r.change_pct === "number") ? r.change_pct : q?.change_pct;
                const prov = q?.provenance ?? r?.provenance ?? null;
                if (errRow && gated) {
                  return (
                    <tr key={sym} className="border-b border-term-border last:border-0 hover:bg-term-panel2">
                      <td className="p-2 font-bold text-term-text">{sym}</td>
                      <td colSpan={4} className="p-2 text-xs text-term-muted">unavailable — {String(q?._error ?? "quote failed").slice(0, 120)} <button type="button" className="ml-2 text-term-green underline" onClick={() => { void quotesQuery.refetch(); }}>Retry</button></td>
                      <td className="p-2"><StatusPill provenance={null} /></td>
                      <td className="p-2 text-right"><button type="button" className="term-btn-ghost text-xs" onClick={() => removeSymbol(sym)} aria-label={`Remove ${sym}`}>REMOVE</button></td>
                    </tr>
                  );
                }
                return (
                  <tr
                    key={sym}
                    className="group cursor-pointer border-b border-term-border transition-colors last:border-0 hover:bg-term-panel2 focus-within:bg-term-panel2"
                    tabIndex={0}
                    onClick={() => openSecurity(q?.symbol ?? sym)}
                    onKeyDown={(e) => { if (e.key === "Enter" && e.target === e.currentTarget) openSecurity(q?.symbol ?? sym); }}
                    aria-label={`${sym} open security brief`}
                  >
                    <td className="p-2">
                      <Link to={`/security/${encodeURIComponent(q?.symbol ?? sym)}`} onClick={(e) => e.stopPropagation()} className="font-bold text-term-green hover:underline">
                        {q?.symbol ?? sym}
                      </Link>
                      <span className="ml-2 hidden text-[11px] text-term-muted lg:inline">{q?.instrument?.company_name ?? r?.instrument?.company_name ?? ""}</span>
                    </td>
                    <td className="tnum term-num p-2 text-right font-semibold text-term-text">
                      <CurrencyValue value={price} currency={ccy} />
                    </td>
                    <td className={`tnum term-num p-2 text-right font-semibold ${changeColor(chg)}`}>
                      {typeof chg === "number" && Number.isFinite(chg) ? `${changeArrow(chg)} ${formatPct1(Math.abs(chg) / 100)}` : "—"}
                    </td>
                    <ForecastCells symbol={q?.symbol ?? sym} />
                    <td className="p-2"><StatusPill provenance={prov} marketState={q?.market_state ?? r?.market_state} /></td>
                    <td className="p-2 text-right">
                      <span className="inline-flex gap-1 opacity-100 focus-within:opacity-100 lg:opacity-0 lg:group-hover:opacity-100 lg:group-focus-within:opacity-100">
                        <Link to={`/security/${encodeURIComponent(q?.symbol ?? sym)}`} onClick={(e) => e.stopPropagation()} className="term-btn-sm" aria-label={`Open ${sym}`}>OPEN</Link>
                        <Link to={`/forecast/${encodeURIComponent(q?.symbol ?? sym)}`} onClick={(e) => e.stopPropagation()} className="term-btn-sm" aria-label={`Research ${sym}`}>RESEARCH</Link>
                        <button type="button" className="term-btn-sm" onClick={(e) => { e.stopPropagation(); removeSymbol(sym); }} aria-label={`Remove ${sym}`}>✕</button>
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div className="flex flex-wrap items-center justify-between gap-2 p-2 text-[11px] text-term-muted">
            <span role="status">{displaySymbols.length} of {symbols.length} shown{filterText ? ` (filter “${filterText}”)` : ""}{gated ? " · native prices" : ` · in ${rankQuery.data?.target_ccy ?? targetCcy}`}</span>
            {lastUpdate && <span>Last successful update: {lastUpdate}</span>}
            <span className="flex gap-2">
              <button type="button" className="term-btn-ghost text-xs" onClick={() => { void quotesQuery.refetch(); void rankQuery.refetch(); }}>RETRY</button>
            </span>
          </div>
        </div>
      )}
      {symbols.length > MAX_WATCHLIST_ROWS && (
        <p className="mt-1 text-[11px] text-term-muted" role="status">showing first {MAX_WATCHLIST_ROWS} of {symbols.length} — remove symbols to narrow the list.</p>
      )}
    </div>
  );
}
export { WatchlistPage as default };
