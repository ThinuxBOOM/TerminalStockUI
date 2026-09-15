import React, { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { displaySymbol, searchInstruments } from "../../api/client";
import Loading from "../../components/Loading";
import ErrorState from "../../components/ErrorState";
import EmptyState from "../../components/EmptyState";
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
  if (c === "CNY") return "\xA5";
  if (c === "EUR") return "\u20AC";
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
  return /* @__PURE__ */ React.createElement("div", { className: "max-w-full" }, /* @__PURE__ */ React.createElement("form", { onSubmit: handleSubmit, className: "flex max-w-full gap-2", role: "search" }, /* @__PURE__ */ React.createElement(
    "input",
    {
      ref: inputRef,
      className: "term-input min-w-0 flex-1",
      value: q,
      onChange: (e) => setQ(e.target.value),
      onKeyDown: handleKeyDown,
      placeholder: "AAPL \xB7 Moutai \xB7 600519.SS \xB7 ASML.AS \u2026",
      "aria-label": "Search instruments",
      role: "combobox",
      "aria-expanded": expanded,
      "aria-controls": "search-listbox",
      "aria-autocomplete": "list",
      "aria-activedescendant": activeIndex >= 0 ? `search-option-${activeIndex}` : void 0,
      spellCheck: false,
      autoComplete: "off"
    }
  ), /* @__PURE__ */ React.createElement(
    "select",
    {
      className: "term-input shrink-0",
      value: market,
      onChange: (e) => setMarket(e.target.value),
      "aria-label": "Filter by market"
    },
    MARKET_OPTIONS.map((m) => /* @__PURE__ */ React.createElement("option", { key: m.label, value: m.value }, m.label))
  ), /* @__PURE__ */ React.createElement("button", { className: "term-btn shrink-0", type: "submit" }, "SEARCH")), !submitted && recent.length > 0 && /* @__PURE__ */ React.createElement("div", { className: "mt-2 flex flex-wrap items-center gap-2 text-xs" }, /* @__PURE__ */ React.createElement("span", { className: "text-term-muted" }, "Recent:"), recent.map((r) => /* @__PURE__ */ React.createElement(
    "button",
    {
      key: r,
      type: "button",
      className: "term-btn-ghost px-2 py-1 text-xs",
      onClick: () => setQ(r),
      "aria-label": `Re-run recent search ${r}`
    },
    r
  )), /* @__PURE__ */ React.createElement(
    "button",
    {
      type: "button",
      className: "text-[11px] text-term-muted hover:text-term-text",
      onClick: clearRecent,
      "aria-label": "Clear recent searches"
    },
    "clear"
  )), /* @__PURE__ */ React.createElement("div", { className: "mt-4 min-w-0" }, !submitted && /* @__PURE__ */ React.createElement("div", { className: "term-panel p-6 text-sm text-term-muted" }, "Type a ticker or company name. Search is exchange-aware (XNYS / XNAS / XSHG / XPAR / XAMS / XBRU). Try ", /* @__PURE__ */ React.createElement("b", { className: "text-term-text" }, "AAPL"), " or", " ", /* @__PURE__ */ React.createElement("b", { className: "text-term-text" }, "Moutai"), "."), submitted && isLoading && /* @__PURE__ */ React.createElement(Loading, { label: `searching \u201C${submitted}\u201D\u2026` }), submitted && data && isFetching && !isLoading && /* @__PURE__ */ React.createElement("p", { className: "mb-2 text-[11px] text-term-muted", role: "status" }, "refreshing\u2026"), submitted && isError && data && data.length > 0 && /* @__PURE__ */ React.createElement("p", { className: "mb-2 text-[11px] text-term-muted", role: "status" }, `search refresh failed (${error instanceof Error ? error.message : "backend unreachable"}) \u2014 showing last loaded results.`), submitted && isError && (!data || data.length === 0) && /* @__PURE__ */ React.createElement(
    ErrorState,
    {
      title: "Search unavailable",
      detail: error instanceof Error ? error.message : "Backend unreachable. Check VITE_API_BASE_URL.",
      onRetry: () => void refetch()
    }
  ), submitted && !isLoading && !isError && (data?.length ?? 0) === 0 && /* @__PURE__ */ React.createElement(
    EmptyState,
    {
      title: `No instruments found for \u201C${submitted}\u201D`,
      detail: "Check spelling or exchange suffix (.SS / .PA / .AS / .BR)."
    }
  ), submitted && !isLoading && !isError && (data?.length ?? 0) > 1 && /* @__PURE__ */ React.createElement(
    "div",
    {
      className: "mb-2 rounded border border-term-amber p-2 text-xs text-term-amber",
      role: "status"
    },
    "Ambiguous \u2014 ",
    data?.length,
    " candidates for \u201C",
    submitted,
    "\u201D. Not auto-resolved; pick the exact symbol or narrow with the market filter."
  ), submitted && data && data.length > 0 && /* @__PURE__ */ React.createElement(React.Fragment, null, totalResults > visibleResults.length && /* @__PURE__ */ React.createElement("p", { className: "mb-2 text-[11px] text-term-muted", role: "status" }, "showing first ", visibleResults.length, " of ", totalResults, " \u2014 refine the query or market filter."), /* @__PURE__ */ React.createElement(
    "ul",
    {
      id: "search-listbox",
      role: "listbox",
      "aria-label": "Search results",
      className: "term-panel divide-y divide-term-border"
    },
    visibleResults.map((r, i) => {
      const sym = displaySymbol(r);
      const curr = (r.currency ?? "").toUpperCase();
      const active = i === activeIndex;
      return /* @__PURE__ */ React.createElement(
        "li",
        {
          key: `${sym}-${r.exchange_mic ?? ""}-${i}`,
          id: `search-option-${i}`,
          role: "option",
          "aria-selected": active,
          className: `flex items-center justify-between gap-2 p-3 ${active ? "bg-term-border" : ""}`
        },
        /* @__PURE__ */ React.createElement("div", { className: "min-w-0" }, /* @__PURE__ */ React.createElement(
          Link,
          {
            to: `/security/${encodeURIComponent(sym)}`,
            className: "font-bold text-term-green hover:underline",
            onClick: () => saveRecent(submitted)
          },
          sym
        ), /* @__PURE__ */ React.createElement("span", { className: "ml-2 text-xs text-term-muted" }, r.company_name ?? "", " ", r.exchange_mic ? `\xB7 ${r.exchange_mic}` : "", " ", curr ? `\xB7 ${curr}${glyphFor(curr)}` : "")),
        /* @__PURE__ */ React.createElement(
          Link,
          {
            className: "term-btn-ghost shrink-0 text-xs",
            to: `/security/${encodeURIComponent(sym)}`,
            onClick: () => saveRecent(submitted),
            "aria-label": `Open Security Brief for ${sym}`
          },
          "BRIEF \u2192"
        )
      );
    })
  ))));
}
export { MARKET_OPTIONS, SearchBox as default, useInstrumentSearch };
