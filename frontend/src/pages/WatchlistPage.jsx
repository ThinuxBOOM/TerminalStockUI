import React, { useCallback, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import useWatchlist from "../hooks/useWatchlist";
import { TARGET_CURRENCIES, getQuote, isFreshFxProvenance, normalizeTargetCcy, rankCrossMarket } from "../api/client";
import CurrencyValue from "../components/CurrencyValue";
import ErrorState from "../components/ErrorState";
import FXProvenanceBanner from "../components/FXProvenanceBanner";
import Loading from "../components/Loading";
import MarketStateBadge from "../components/MarketStateBadge";
import ProvenanceBadge from "../components/ProvenanceBadge";
const GATE_MESSAGE = "Cross-market comparison unavailable \u2014 FX provenance missing";
const MAX_WATCHLIST_ROWS = 100;
function normalizeSymbolInput(v) {
  return v.trim().toUpperCase().replace(/\s+/g, "");
}
async function fetchNativeQuotes(symbols, signal) {
  const settled = await Promise.all(
    symbols.map(async (s) => {
      try {
        return await getQuote(s, void 0, { signal });
      } catch {
        return null;
      }
    })
  );
  return settled;
}
function WatchlistPage() {
  const { symbols, add, remove, clear } = useWatchlist();
  const [draft, setDraft] = useState("");
  const [targetCcy, setTargetCcy] = useState("USD");
  // Sorted key: reordering the watchlist must not bust the query cache.
  // Use the SAME sorted array in queryFn so rows[i] always aligns.
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
  const fxProvenance = rankQuery.data?.fx_provenance ?? null;
  const fxFresh = rankQuery.data ? isFreshFxProvenance(fxProvenance) : false;
  const gated = rankQuery.isError || !rankQuery.data || !fxFresh;
  const visibleSymbols = useMemo(
    () => symbols.slice(0, MAX_WATCHLIST_ROWS),
    [symbols]
  );
  const symbolsOverflow = symbols.length > visibleSymbols.length;
  const rankedRows = useMemo(() => {
    const list = rankQuery.data?.ranking ?? [];
    return [...list].sort((a, b) => {
      const av = a.converted_price ?? null;
      const bv = b.converted_price ?? null;
      if (av === null && bv === null) return 0;
      if (av === null) return 1;
      if (bv === null) return -1;
      return bv - av;
    });
  }, [rankQuery.data]);
  const visibleRanked = useMemo(
    () => rankedRows.slice(0, MAX_WATCHLIST_ROWS),
    [rankedRows]
  );
  const rankedOverflow = rankedRows.length > visibleRanked.length;
  function addSymbol() {
    const sym = normalizeSymbolInput(draft);
    if (!sym) return;
    add(sym, "manual");
    setDraft("");
  }
  const removeSymbol = useCallback((sym) => remove(sym), [remove]);
  const clearWatchlist = useCallback(() => clear(), [clear]);
  return /* @__PURE__ */ React.createElement("div", null, /* @__PURE__ */ React.createElement("h1", { className: "mb-3 text-sm tracking-widest text-term-muted" }, "WATCHLIST \xB7 CROSS-MARKET (FX-GATED)"), /* @__PURE__ */ React.createElement("div", { className: "mb-3 flex flex-wrap items-center gap-2" }, /* @__PURE__ */ React.createElement("label", { htmlFor: "target-ccy", className: "text-xs text-term-muted" }, "Target currency"), /* @__PURE__ */ React.createElement(
    "select",
    {
      id: "target-ccy",
      className: "term-input",
      value: targetCcy,
      onChange: (e) => setTargetCcy(normalizeTargetCcy(e.target.value)),
      "aria-label": "Target currency for cross-market comparison"
    },
    TARGET_CURRENCIES.map((c) => /* @__PURE__ */ React.createElement("option", { key: c, value: c }, c))
  ), /* @__PURE__ */ React.createElement(
    "form",
    {
      className: "flex flex-wrap gap-2",
      onSubmit: (e) => {
        e.preventDefault();
        addSymbol();
      }
    },
    /* @__PURE__ */ React.createElement(
      "input",
      {
        className: "term-input min-w-0 flex-1",
        value: draft,
        onChange: (e) => setDraft(e.target.value),
        placeholder: "Add symbol (e.g. MC.PA)",
        "aria-label": "Add symbol to watchlist"
      }
    ),
    /* @__PURE__ */ React.createElement("button", { className: "term-btn", type: "submit" }, "ADD")
  ), symbols.length > 0 && /* @__PURE__ */ React.createElement(
    "button",
    {
      className: "term-btn-ghost text-xs",
      type: "button",
      onClick: clearWatchlist,
      "aria-label": "Clear watchlist"
    },
    "CLEAR"
  )), /* @__PURE__ */ React.createElement(
    FXProvenanceBanner,
    {
      provenance: fxProvenance,
      targetCcy,
      loading: rankQuery.isLoading,
      error: rankQuery.error
    }
  ), gated ? /* @__PURE__ */ React.createElement("div", null, /* @__PURE__ */ React.createElement(
    "div",
    {
      className: "term-panel border-term-red p-4 text-sm text-term-red",
      role: "alert"
    },
    GATE_MESSAGE,
    rankQuery.error ? /* @__PURE__ */ React.createElement("span", { className: "mt-1 block text-xs text-term-muted" }, rankQuery.error instanceof Error ? rankQuery.error.message : "FX rank endpoint unreachable.", " ", "Showing native-currency quotes only; no conversion applied.") : /* @__PURE__ */ React.createElement("span", { className: "mt-1 block text-xs text-term-muted" }, "Ranked conversion needs fresh FX (grade A/B, delay \u2264 30m, no fallback). Showing native-currency quotes only; no conversion applied.")
  ), /* @__PURE__ */ React.createElement("div", { className: "mt-4" }, renderNativeQuotes())) : /* @__PURE__ */ React.createElement("div", null, renderRanked()));
  function renderNativeQuotes() {
    if (quotesQuery.isLoading) return /* @__PURE__ */ React.createElement(Loading, { label: "loading watchlist quotes\u2026" });
    if (quotesQuery.isError) {
      return /* @__PURE__ */ React.createElement(
        ErrorState,
        {
          title: "Watchlist quotes unavailable",
          detail: quotesQuery.error instanceof Error ? quotesQuery.error.message : "Backend unreachable. Check VITE_API_BASE_URL.",
          onRetry: () => void quotesQuery.refetch()
        }
      );
    }
    const rows = quotesQuery.data ?? [];
    if (rows.length === 0) {
      return /* @__PURE__ */ React.createElement("div", { className: "term-panel p-6 text-sm text-term-muted" }, "Watchlist is empty. Add a symbol (e.g. MC.PA, ASML.AS, UCB.BR).");
    }
    return /* @__PURE__ */ React.createElement("ul", { className: "term-panel-hero divide-y divide-term-border" }, symbolsOverflow && /* @__PURE__ */ React.createElement("li", { className: "p-2 text-[11px] text-term-muted", role: "status" }, "showing first ", visibleSymbols.length, " of ", symbols.length, " \u2014 remove symbols to narrow the list."), visibleSymbols.map((sym, i) => {
      const q = rows[i] ?? null;
      if (!q) {
        return /* @__PURE__ */ React.createElement("li", { key: sym, className: "flex items-center justify-between p-3 even:bg-term-panel2 transition-colors duration-150" }, /* @__PURE__ */ React.createElement("div", null, /* @__PURE__ */ React.createElement(Link, { to: `/security/${encodeURIComponent(sym)}`, className: "font-bold text-term-green hover:underline" }, sym), /* @__PURE__ */ React.createElement("span", { className: "ml-2 text-xs text-term-muted" }, "unavailable")), /* @__PURE__ */ React.createElement(
          "button",
          {
            className: "term-btn-ghost text-xs",
            type: "button",
            onClick: () => removeSymbol(sym),
            "aria-label": `Remove ${sym}`
          },
          "REMOVE"
        ));
      }
      return /* @__PURE__ */ React.createElement("li", { key: `${sym}-${q.instrument?.exchange_mic ?? ""}`, className: "p-3 even:bg-term-panel2 transition-colors duration-150" }, /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center justify-between gap-2" }, /* @__PURE__ */ React.createElement("div", null, /* @__PURE__ */ React.createElement(Link, { to: `/security/${encodeURIComponent(q.symbol)}`, className: "font-bold text-term-green hover:underline" }, q.symbol), /* @__PURE__ */ React.createElement("span", { className: "ml-2 text-xs text-term-muted" }, q.instrument?.company_name ?? "", " ", q.instrument?.exchange_mic ? `\xB7 ${q.instrument.exchange_mic}` : "", " ", q.currency ? `\xB7 ${q.currency}` : ""), /* @__PURE__ */ React.createElement("span", { className: "ml-2 text-sm" }, /* @__PURE__ */ React.createElement(CurrencyValue, { value: q.price, currency: q.currency ?? "USD" })), /* @__PURE__ */ React.createElement("span", { className: "ml-2" }, /* @__PURE__ */ React.createElement(MarketStateBadge, { state: q.market_state, provenance: q.provenance }))), /* @__PURE__ */ React.createElement(
        "button",
        {
          className: "term-btn-ghost text-xs",
          type: "button",
          onClick: () => removeSymbol(sym),
          "aria-label": `Remove ${sym}`
        },
        "REMOVE"
      )), /* @__PURE__ */ React.createElement("div", { className: "mt-1" }, /* @__PURE__ */ React.createElement(ProvenanceBadge, { p: q.provenance })));
    }));
  }
  function renderRanked() {
    if (rankQuery.isLoading && !rankQuery.data) return /* @__PURE__ */ React.createElement(Loading, { label: `ranking in ${targetCcy}\u2026` });
    const data = rankQuery.data;
    if (!data) return null;
    if (!isFreshFxProvenance(data.fx_provenance)) {
      return /* @__PURE__ */ React.createElement("div", { className: "term-panel border-term-red p-4 text-sm text-term-red", role: "alert" }, GATE_MESSAGE);
    }
    const rows = visibleRanked;
    return /* @__PURE__ */ React.createElement("div", { className: "term-panel-hero overflow-x-auto" }, rankedOverflow && /* @__PURE__ */ React.createElement("p", { className: "p-2 text-[11px] text-term-muted", role: "status" }, "showing first ", visibleRanked.length, " of ", rankedRows.length, " \u2014 remove symbols to narrow the list."), /* @__PURE__ */ React.createElement("table", { className: "w-full text-sm" }, /* @__PURE__ */ React.createElement("caption", { className: "sr-only" }, "Watchlist ranked by converted price"), /* @__PURE__ */ React.createElement("thead", { className: "sticky top-0 bg-term-panel z-10" }, /* @__PURE__ */ React.createElement("tr", { className: "border-b border-term-border text-left text-xs text-term-muted" }, /* @__PURE__ */ React.createElement("th", { scope: "col", className: "p-2" }, "#"), /* @__PURE__ */ React.createElement("th", { scope: "col", className: "p-2" }, "Symbol"), /* @__PURE__ */ React.createElement("th", { scope: "col", className: "p-2 text-right" }, "Native"), /* @__PURE__ */ React.createElement("th", { scope: "col", className: "p-2 text-right" }, "Converted (", data.target_ccy, ")"), /* @__PURE__ */ /* @__PURE__ */ React.createElement("th", { scope: "col", className: "p-2" }, "FX provenance"), /* @__PURE__ */ React.createElement("th", { scope: "col", className: "p-2" }, /* @__PURE__ */ React.createElement("span", { className: "sr-only" }, "Remove")))), /* @__PURE__ */ React.createElement("tbody", null, rows.map((r, idx) => /* @__PURE__ */ React.createElement("tr", { key: `${r.symbol}-${idx}`, className: "border-b border-term-border even:bg-term-panel2 transition-colors duration-150" }, /* @__PURE__ */ React.createElement("td", { className: "p-2 text-term-muted" }, idx + 1), /* @__PURE__ */ React.createElement("td", { className: "p-2" }, /* @__PURE__ */ React.createElement(Link, { to: `/security/${encodeURIComponent(r.symbol)}`, className: "font-bold text-term-green hover:underline" }, r.symbol), /* @__PURE__ */ React.createElement("span", { className: "ml-2 text-xs text-term-muted" }, r.instrument?.company_name ?? "", " ", r.instrument?.exchange_mic ? `\xB7 ${r.instrument.exchange_mic}` : "")), /* @__PURE__ */ React.createElement("td", { className: "p-2 text-right term-num" }, /* @__PURE__ */ React.createElement(CurrencyValue, { value: r.price ?? null, currency: r.currency ?? "USD" })), /* @__PURE__ */ React.createElement("td", { className: "p-2 text-right term-num" }, /* @__PURE__ */ React.createElement("b", null, /* @__PURE__ */ React.createElement(
      CurrencyValue,
      {
        value: r.converted_price ?? null,
        currency: data.target_ccy
      }
    ))), /* @__PURE__ */ React.createElement("td", { className: "p-2" }, /* @__PURE__ */ React.createElement(ProvenanceBadge, { p: data.fx_provenance ?? r.provenance })), /* @__PURE__ */ React.createElement("td", { className: "p-2" }, /* @__PURE__ */ React.createElement(
      "button",
      {
        className: "term-btn-ghost text-xs",
        type: "button",
        onClick: () => removeSymbol(r.symbol),
        "aria-label": `Remove ${r.symbol}`
      },
      "REMOVE"
    )))))), /* @__PURE__ */ React.createElement("p", { className: "p-2 text-[11px] text-term-muted" }, "Ranked by converted price in ", data.target_ccy, " \xB7 FX", " ", data.fx_provenance ? `${data.fx_provenance.source} as_of ${data.fx_provenance.as_of}` : "provenance unavailable", " ", "\xB7 provenance badge is the conversion FX envelope; native quotes per symbol."));
  }
}
export { WatchlistPage as default };
