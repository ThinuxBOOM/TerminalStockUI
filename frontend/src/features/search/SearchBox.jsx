import React, { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { displaySymbol, searchInstruments } from "../../api/client";
import Loading from "../../components/Loading";
import ErrorState from "../../components/ErrorState";
import EmptyState from "../../components/EmptyState";
// XCOL omitted intentionally until the venue is enabled in config/markets.yaml
// (disabled probe slot — present in MARKET_LABELS/MARKET_CURRENCIES but not selectable).
const MARKET_OPTIONS = [
  { label: "All", value: "" },
  { label: "NYSE", value: "XNYS" },
  { label: "NASDAQ", value: "XNAS" },
  { label: "SSE", value: "XSHG" },
  { label: "Euronext Paris", value: "XPAR" },
  { label: "Euronext Amsterdam", value: "XAMS" },
  { label: "Euronext Brussels", value: "XBRU" }
];
const RECENT_KEY = "onemarket.recentSearches.v1";
const RECENT_MAX = 6;
const DEBOUNCE_MS = 300;
function loadRecent() {
  try {
    const raw = localStorage.getItem(RECENT_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.map((s) => String(s ?? "").trim()).filter(Boolean).slice(0, RECENT_MAX);
  } catch {
    return [];
  }
}
function glyphFor(currency) {
  const c = (currency ?? "").trim().toUpperCase();
  if (c === "USD") return "$";
  if (c === "CNY") return "¥";
  if (c === "EUR") return "€";
  return "";
}
function useInstrumentSearch(query, market, enabled, limit = 50, offset = 0) {
  const mic = market.trim().toUpperCase();
  // Normalized key: "aapl" and "AAP " share one cache entry.
  const key = query.trim().toUpperCase();
  const lim = Math.min(50, Math.max(1, Number(limit) || 50));
  const off = Math.min(200, Math.max(0, Number(offset) || 0));
  return useQuery({
    queryKey: ["instruments", "search", key || "(empty)", mic || "ALL", lim, off],
    queryFn: ({ signal }) => searchInstruments(query, mic || void 0, { signal, limit: lim, offset: off }),
    enabled,
    staleTime: 6e4,
    gcTime: 3e5,
    retry: false,
    placeholderData: keepPreviousData
  });
}
function SearchBox({
  initial = "",
  initialMarket = ""
}) {
  const [q, setQ] = useState(initial);
  const [market, setMarket] = useState(initialMarket);
  const [debounced, setDebounced] = useState(initial);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [recent, setRecent] = useState(loadRecent);
  const navigate = useNavigate();
  const inputRef = useRef(null);
  useEffect(() => {
    const t = setTimeout(() => {
      setDebounced(q);
      setActiveIndex(-1);
    }, DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [q]);
  useEffect(() => {
    setQ(initial);
    setDebounced(initial);
  }, [initial]);
  useEffect(() => {
    setMarket(initialMarket);
  }, [initialMarket]);
  const submitted = debounced.trim();
  const mic = market.trim().toUpperCase();
  const { data, isLoading, isFetching, isError, error, refetch } = useInstrumentSearch(
    submitted,
    mic,
    // Single characters match half the registry: wait for 2+.
    submitted.length >= 2
  );
  const MAX_RESULTS = 50;
  const totalResults = data?.length ?? 0;
  const visibleResults = useMemo(
    () => (data ?? []).slice(0, MAX_RESULTS),
    [data]
  );
  function saveRecent(term) {
    const t = term.trim();
    if (!t) return;
    setRecent((prev) => {
      const next = [t, ...prev.filter((s) => s.toLowerCase() !== t.toLowerCase())].slice(
        0,
        RECENT_MAX
      );
      try {
        localStorage.setItem(RECENT_KEY, JSON.stringify(next));
      } catch {
      }
      return next;
    });
  }
  function clearRecent() {
    setRecent([]);
    try {
      localStorage.removeItem(RECENT_KEY);
    } catch {
    }
  }
  function goToSymbol(sym) {
    saveRecent(q.trim() || sym);
    navigate(`/security/${encodeURIComponent(sym)}`);
  }
  function handleSubmit(e) {
    e.preventDefault();
    const term = q.trim();
    if (!term) return;
    setDebounced(term);
    saveRecent(term);
    // Search only: never auto-navigate. The user picks an explicit row
    // (click BRIEF or arrow-highlight + Enter) so ambiguous symbols are
    // disambiguated instead of silently jumping to the first hit.
    if (visibleResults.length === 0) {
      void refetch();
    }
  }
  function handleKeyDown(e) {
    const n = visibleResults.length;
    if (e.key === "ArrowDown" && n > 0) {
      e.preventDefault();
      setActiveIndex((i) => (i + 1) % n);
    } else if (e.key === "ArrowUp" && n > 0) {
      e.preventDefault();
      setActiveIndex((i) => i <= 0 ? n - 1 : i - 1);
    } else if (e.key === "Enter" && n > 0 && q.trim() && activeIndex >= 0 && activeIndex < n) {
      // Navigate only on an explicitly highlighted row; plain Enter
      // just runs the search (see handleSubmit).
      e.preventDefault();
      goToSymbol(displaySymbol(visibleResults[activeIndex]));
    }
  }
  const expanded = submitted.length >= 2 && (data?.length ?? 0) > 0;
  return (
    <div className="max-w-full">
      <section className="term-panel p-4" aria-label="Search and filters">
        <form onSubmit={handleSubmit} className="flex max-w-full flex-col gap-2 sm:flex-row" role="search">
          <div className="min-w-0 flex-1">
            <label htmlFor="discover-q" className="term-label">Symbol or company</label>
            <input
              id="discover-q"
              ref={inputRef}
              className="term-input mt-1 min-w-0 w-full"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="AAPL · Moutai · 600519.SS · ASML.AS …"
              aria-label="Search instruments"
              role="combobox"
              aria-expanded={expanded}
              aria-controls="search-listbox"
              aria-autocomplete="list"
              aria-activedescendant={activeIndex >= 0 ? `search-option-${activeIndex}` : void 0}
              spellCheck={false}
              autoComplete="off"
            />
          </div>
          <div className="shrink-0 sm:w-52">
            <label htmlFor="discover-market" className="term-label">Market</label>
            <select id="discover-market" className="term-input mt-1 w-full" value={market} onChange={(e) => setMarket(e.target.value)} aria-label="Filter by market">
              {MARKET_OPTIONS.map((m) => <option key={m.label} value={m.value}>{m.label}</option>)}
            </select>
          </div>
          <div className="flex shrink-0 items-end">
            <button className="term-btn w-full sm:w-auto" type="submit">SEARCH</button>
          </div>
        </form>
        {!submitted && recent.length > 0 && (
          <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
            <span className="term-label">Recent:</span>
            {recent.map((r) => (
              <button key={r} type="button" className="term-btn-ghost px-2 py-1 text-xs" onClick={() => setQ(r)} aria-label={`Re-run recent search ${r}`}>{r}</button>
            ))}
            <button type="button" className="text-[11px] text-term-muted hover:text-term-text" onClick={clearRecent} aria-label="Clear recent searches">clear</button>
          </div>
        )}
      </section>
      <div className="mt-4 min-w-0" role="region" aria-label="Search results" aria-live="polite">
        {!submitted && (
          <div className="term-panel p-6 text-sm text-term-muted">Type a ticker or company name. Search is exchange-aware (XNYS / XNAS / XSHG / XPAR / XAMS / XBRU). Try <b className="text-term-text">AAPL</b> or <b className="text-term-text">Moutai</b>.</div>
        )}
        {submitted && submitted.length < 2 && <div className="term-panel p-6 text-sm text-term-muted">Type 2+ characters to search.</div>}
        {submitted && submitted.length >= 2 && isLoading && <Loading label={`searching “${submitted}”…`} />}
        {submitted && submitted.length >= 2 && data && isFetching && !isLoading && <p className="mb-2 text-[11px] text-term-muted" role="status">refreshing…</p>}
        {submitted && submitted.length >= 2 && isError && data && data.length > 0 && <p className="mb-2 text-[11px] text-term-muted" role="status">{`search refresh failed (${error instanceof Error ? error.message : "backend unreachable"}) — showing last loaded results.`}</p>}
        {submitted && submitted.length >= 2 && isError && (!data || data.length === 0) && (
          <ErrorState title="Search unavailable" detail={error instanceof Error ? error.message : "Backend unreachable. Check VITE_API_BASE_URL."} onRetry={() => void refetch()} />
        )}
        {submitted && submitted.length >= 2 && !isLoading && !isFetching && !isError && (data?.length ?? 0) === 0 && data !== void 0 && (
          <EmptyState title={`No instruments found for “${submitted}”`} detail="Check spelling or exchange suffix (.SS / .PA / .AS / .BR)." />
        )}
        {submitted && submitted.length >= 2 && !isLoading && !isFetching && !isError && data === void 0 && <Loading label={`searching “${submitted}”…`} />}
        {submitted && submitted.length >= 2 && !isLoading && !isError && (data?.length ?? 0) > 1 && (
          <div className="mb-2 rounded border border-term-border p-2 text-xs text-term-amber" role="status">
            Ambiguous — {data?.length} candidates for “{submitted}”. Not auto-resolved; pick the exact symbol or narrow with the market filter.
          </div>
        )}
        {submitted && data && data.length > 0 && (
          <>
            {totalResults > visibleResults.length && <p className="mb-2 text-[11px] text-term-muted" role="status">showing first {visibleResults.length} of {totalResults} — refine the query or market filter.</p>}
            <p className="mb-2 text-[11px] text-term-muted" role="status">{visibleResults.length} result{visibleResults.length === 1 ? "" : "s"} for “{submitted}”{mic ? ` in ${mic}` : ""} — ↑↓ to highlight, Enter to open.</p>
            <div className="term-panel-hero overflow-x-auto">
              <ul id="search-listbox" role="listbox" aria-label="Search results" className="min-w-[560px] divide-y divide-term-border">
                {visibleResults.map((r, i) => {
                  const sym = displaySymbol(r);
                  if (!sym) return null;
                  const curr = (r.currency ?? "").toUpperCase();
                  const active = i === activeIndex;
                  return (
                    <li key={`${sym}-${r.exchange_mic ?? ""}-${i}`} id={`search-option-${i}`} role="option" aria-selected={active} className={`flex items-center justify-between gap-2 p-3 transition-colors hover:bg-term-panel2 focus-within:bg-term-panel2 ${active ? "bg-term-panel2 outline outline-1 outline-term-green" : ""}`}>
                      <div className="min-w-0">
                        <Link to={`/security/${encodeURIComponent(sym)}`} className="font-bold text-term-green hover:underline focus-visible:underline" onClick={() => saveRecent(submitted)}>{sym}</Link>
                        <span className="ml-2 truncate text-xs text-term-muted">{r.company_name ?? ""} {r.exchange_mic ? `· ${r.exchange_mic}` : ""} {curr ? `· ${curr}${glyphFor(curr)}` : ""}</span>
                      </div>
                      <Link className="term-btn-ghost shrink-0 text-xs" to={`/security/${encodeURIComponent(sym)}`} onClick={() => saveRecent(submitted)} aria-label={`Open Security Brief for ${sym}`}>BRIEF →</Link>
                    </li>
                  );
                })}
              </ul>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
export { MARKET_OPTIONS, SearchBox as default, useInstrumentSearch };
