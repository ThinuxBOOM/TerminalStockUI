import React, { memo, useCallback, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getAuditForecasts, getHealth, getProvidersHealth, getQuote } from "../api/client";
import ProvenanceBadge from "../components/ProvenanceBadge";
import FreshnessBadge from "../components/FreshnessBadge";
import MarketStateBadge from "../components/MarketStateBadge";
import MarketLiquidityPanel from "../components/MarketLiquidityPanel";
import { useMarketLiquidity } from "../hooks/useMarketLiquidity";
import useWatchlist from "../hooks/useWatchlist";
import CurrencyValue from "../components/CurrencyValue";
import Skeleton from "../components/Skeleton";
import EmptyState from "../components/EmptyState";
import ErrorState, { StaleBanner } from "../components/ErrorState";
const VENUES = [
  { mic: "XNYS", label: "NYSE (XNYS)", symbol: "JPM" },
  { mic: "XNAS", label: "NASDAQ (XNAS)", symbol: "AAPL" },
  { mic: "XSHG", label: "SSE (XSHG)", symbol: "600519.SS" },
  { mic: "XPAR", label: "Euronext Paris (XPAR)", symbol: "MC.PA" },
  { mic: "XAMS", label: "Euronext Amsterdam (XAMS)", symbol: "ASML.AS" },
  { mic: "XBRU", label: "Euronext Brussels (XBRU)", symbol: "UCB.BR" }
];
function normalizeSymbolInput(v) {
  return v.trim().toUpperCase().replace(/\s+/g, "");
}
const MAX_HOME_WATCHLIST = 50;
const MAX_HOME_REPORTS = 20;
// Memoized: props are primitives, so parent re-renders (draft keystrokes)
// skip these rows entirely.
const VenueRow = memo(function VenueRow({ label, symbol }) {
  const q = useQuery({
    queryKey: ["quote", symbol],
    queryFn: ({ signal }) => getQuote(symbol, void 0, { signal }),
    retry: false,
    staleTime: 3e4
  });
  return /* @__PURE__ */ React.createElement("li", { className: "flex items-center justify-between gap-2 border-b border-term-border pb-1" }, /* @__PURE__ */ React.createElement("span", { className: "min-w-0 truncate" }, label), q.isLoading && /* @__PURE__ */ React.createElement("span", { className: "text-xs text-term-muted", role: "status" }, "\u2026"), q.isError && /* @__PURE__ */ React.createElement("span", { className: "text-xs text-term-muted", role: "status" }, "unavailable"), q.data && /* @__PURE__ */ React.createElement("span", { className: "flex shrink-0 items-center gap-2" }, /* @__PURE__ */ React.createElement(MarketStateBadge, { state: q.data.market_state, provenance: q.data.provenance }), /* @__PURE__ */ React.createElement("span", { className: "hidden text-[10px] text-term-muted lg:inline" }, typeof q.data.provenance?.delay_minutes === "number" ? `${q.data.provenance.delay_minutes}m` : "\u2014")));
});
function WatchlistRowInner({
  symbol,
  onRemove
}) {
  const q = useQuery({
    queryKey: ["quote", symbol],
    queryFn: ({ signal }) => getQuote(symbol, void 0, { signal }),
    retry: false,
    staleTime: 3e4
  });
  if (q.isLoading)
    return /* @__PURE__ */ React.createElement("li", { className: "p-3 text-xs text-term-muted", role: "status" }, "\u2026 ", symbol);
  if (q.isError || !q.data)
    return /* @__PURE__ */ React.createElement("li", { className: "flex items-center justify-between gap-2 p-3 text-xs" }, /* @__PURE__ */ React.createElement("span", { className: "min-w-0 truncate text-term-muted" }, symbol, " \u2014 unavailable"), /* @__PURE__ */ React.createElement("span", { className: "flex shrink-0 gap-2" }, /* @__PURE__ */ React.createElement(Link, { className: "text-term-green", to: `/security/${encodeURIComponent(symbol)}` }, "BRIEF \u2192"), /* @__PURE__ */ React.createElement(
      "button",
      {
        type: "button",
        className: "text-term-muted hover:text-term-red",
        onClick: () => onRemove(symbol),
        "aria-label": `Remove ${symbol} from watchlist`
      },
      "\u2715"
    )));
  const d = q.data;
  return /* @__PURE__ */ React.createElement("li", { className: "p-3" }, /* @__PURE__ */ React.createElement("div", { className: "flex items-center justify-between gap-2 text-sm" }, /* @__PURE__ */ React.createElement(
    Link,
    {
      to: `/security/${encodeURIComponent(symbol)}`,
      className: "min-w-0 truncate font-bold text-term-green hover:underline"
    },
    d.symbol,
    " \xB7",
    " ",
    /* @__PURE__ */ React.createElement(CurrencyValue, { value: d.price, currency: d.currency ?? "USD" })
  ), /* @__PURE__ */ React.createElement("span", { className: "flex shrink-0 items-center gap-2" }, /* @__PURE__ */ React.createElement(FreshnessBadge, { p: d.provenance }), /* @__PURE__ */ React.createElement(
    "button",
    {
      type: "button",
      className: "text-xs text-term-muted hover:text-term-red",
      onClick: () => onRemove(symbol),
      "aria-label": `Remove ${symbol} from watchlist`
    },
    "\u2715"
  ))), /* @__PURE__ */ React.createElement("div", { className: "mt-1 flex flex-wrap items-center gap-2" }, /* @__PURE__ */ React.createElement(MarketStateBadge, { state: d.market_state, provenance: d.provenance }), /* @__PURE__ */ React.createElement(ProvenanceBadge, { p: d.provenance })));
}
const WatchlistRow = memo(WatchlistRowInner);
function HomePage() {
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth, retry: false });
  const providers = useQuery({
    queryKey: ["providers-health"],
    queryFn: getProvidersHealth,
    retry: false,
    staleTime: 3e4
  });
  const research = useQuery({
    queryKey: ["audit-forecasts", "recent"],
    queryFn: () => getAuditForecasts(5),
    retry: false,
    staleTime: 6e4
  });
  const liquidity = useMarketLiquidity();
  const { symbols: watchlist, add: addWatchSymbol, remove: removeWatchSymbol } = useWatchlist();
  const [draft, setDraft] = useState("");
  function addSymbol() {
    const sym = normalizeSymbolInput(draft);
    if (!sym) return;
    addWatchSymbol(sym, "manual");
    setDraft("");
  }
  const removeSymbol = useCallback(
    (sym) => {
      removeWatchSymbol(sym);
    },
    [removeWatchSymbol]
  );
  const degraded = health.isError || health.data?.status !== "ok";
  const providerRows = providers.data ?? null;
  const healthProviders = useMemo(() => health.data?.providers ?? [], [health.data]);
  const showProviders = useMemo(
    () => providerRows ?? healthProviders.map((p) => ({
      name: p.name,
      status: p.status,
      latency_ms: p.latency_ms,
      latency_p50_ms: p.latency_ms,
      latency_p95_ms: void 0,
      error_rate_1h: void 0,
      calls_1h: void 0,
      total_calls: void 0,
      circuit: void 0,
      last_check: p.last_check
    })),
    [providerRows, healthProviders]
  );
  const reports = useMemo(() => research.data?.forecasts ?? [], [research.data]);
  return /* @__PURE__ */ React.createElement("div", { className: "grid max-w-full gap-4 md:grid-cols-3" }, /* @__PURE__ */ React.createElement("section", { className: "term-panel min-w-0 p-4", "aria-labelledby": "home-market-status" }, /* @__PURE__ */ React.createElement("h2", { id: "home-market-status", className: "term-label" }, "Market status"), /* @__PURE__ */ React.createElement("ul", { className: "mt-2 space-y-1 text-sm" }, VENUES.map((v) => /* @__PURE__ */ React.createElement(VenueRow, { key: v.mic, label: v.label, symbol: v.symbol }))), /* @__PURE__ */ React.createElement("p", { className: "mt-2 text-[10px] text-term-muted" }, "Live per-venue state from quote market_state + health \u2014 never hardcoded.")), /* @__PURE__ */ React.createElement(
    MarketLiquidityPanel,
    {
      data: liquidity.data ?? null,
      isLoading: liquidity.isLoading,
      isError: liquidity.isError,
      error: liquidity.error,
      onRetry: () => void liquidity.refetch()
    }
  ), /* @__PURE__ */ React.createElement("section", { className: "term-panel min-w-0 p-4", "aria-labelledby": "home-watchlist" }, /* @__PURE__ */ React.createElement("div", { className: "flex items-center justify-between gap-2" }, /* @__PURE__ */ React.createElement("h2", { id: "home-watchlist", className: "term-label" }, "Watchlist"), /* @__PURE__ */ React.createElement(Link, { to: "/watchlist", className: "text-xs text-term-green" }, "ALL \u2192")), /* @__PURE__ */ React.createElement(
    "form",
    {
      className: "mt-2 flex gap-2",
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
        "aria-label": "Add symbol to watchlist",
        spellCheck: false
      }
    ),
    /* @__PURE__ */ React.createElement("button", { className: "term-btn shrink-0", type: "submit" }, "ADD")
  ), watchlist.length === 0 ? /* @__PURE__ */ React.createElement("div", { className: "mt-2" }, /* @__PURE__ */ React.createElement(
    EmptyState,
    {
      title: "Watchlist is empty",
      detail: "Add a symbol above \u2014 it persists in this browser."
    }
  )) : /* @__PURE__ */ React.createElement("ul", { className: "mt-2 divide-y divide-term-border" }, watchlist.slice(0, MAX_HOME_WATCHLIST).map((s) => /* @__PURE__ */ React.createElement(WatchlistRow, { key: s, symbol: s, onRemove: removeSymbol }))), watchlist.length > MAX_HOME_WATCHLIST && /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-[11px] text-term-muted", role: "status" }, "showing first ", MAX_HOME_WATCHLIST, " of ", watchlist.length, " \u2014", " ", /* @__PURE__ */ React.createElement(Link, { to: "/watchlist", className: "text-term-green" }, "open full watchlist \u2192"))), /* @__PURE__ */ React.createElement("div", { className: "min-w-0 space-y-4" }, /* @__PURE__ */ React.createElement("section", { className: "term-panel min-w-0 p-4", "aria-labelledby": "home-provider-health" }, /* @__PURE__ */ React.createElement("h2", { id: "home-provider-health", className: "term-label" }, "Provider health"), (degraded || providers.isError) && /* @__PURE__ */ React.createElement("div", { className: "mt-2" }, /* @__PURE__ */ React.createElement(StaleBanner, { detail: "health endpoint degraded \u2014 cached values shown" })), providers.isLoading || health.isLoading ? /* @__PURE__ */ React.createElement("div", { className: "mt-2" }, /* @__PURE__ */ React.createElement(Skeleton, { label: "loading provider health\u2026", lines: 3 })) : null, showProviders.length > 0 ? /* @__PURE__ */ React.createElement("ul", { className: "mt-2 space-y-1 text-xs" }, showProviders.map((p) => {
    const latency = p.latency_p50_ms ?? p.latency_ms ?? p.latency_p95_ms;
    const bad = p.status !== "ok" || p.circuit !== void 0 && p.circuit === "open";
    return /* @__PURE__ */ React.createElement(
      "li",
      {
        key: p.name,
        className: "flex justify-between gap-2 border-b border-term-border pb-1"
      },
      /* @__PURE__ */ React.createElement("span", { className: "min-w-0 truncate" }, p.name),
      /* @__PURE__ */ React.createElement("span", { className: bad ? "text-term-red" : "text-term-green" }, p.status, p.circuit ? ` \xB7 ${p.circuit}` : "", latency !== void 0 ? ` \xB7 ${latency}ms` : "")
    );
  })) : !health.isLoading && !providers.isLoading && /* @__PURE__ */ React.createElement("p", { className: "mt-2 text-xs text-term-muted" }, "yfinance \xB7 AKShare \xB7 FX \u2014 no live data (backend offline?)."), providers.isError && /* @__PURE__ */ React.createElement("p", { className: "mt-2 text-[11px] text-term-amber" }, "\u26A0 /api/providers/health unreachable", providers.error instanceof Error ? ` (${providers.error.message})` : "", " \u2014 showing /health summary.")), /* @__PURE__ */ React.createElement("section", { className: "term-panel min-w-0 p-4", "aria-labelledby": "home-research" }, /* @__PURE__ */ React.createElement("h2", { id: "home-research", className: "term-label" }, "Latest research"), research.isLoading && /* @__PURE__ */ React.createElement("div", { className: "mt-2" }, /* @__PURE__ */ React.createElement(Skeleton, { label: "loading latest research\u2026", lines: 3 })), research.isError && /* @__PURE__ */ React.createElement("div", { className: "mt-2" }, /* @__PURE__ */ React.createElement(
    ErrorState,
    {
      title: "Research feed unavailable",
      detail: research.error instanceof Error ? research.error.message : "Backend /api/audit/forecasts unreachable.",
      onRetry: () => void research.refetch()
    }
  )), !research.isLoading && !research.isError && reports.length === 0 && /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-xs text-term-muted" }, "No reports yet. Scheduled reports land here (Milestone 4+)."), !research.isLoading && !research.isError && reports.length > 0 && /* @__PURE__ */ React.createElement("ul", { className: "mt-2 space-y-1 text-xs" }, reports.slice(0, MAX_HOME_REPORTS).map((r, i) => /* @__PURE__ */ React.createElement(
    "li",
    {
      key: r.forecast_id ?? `${r.symbol ?? "unknown"}-${i}`,
      className: "flex items-center justify-between gap-2 border-b border-term-border pb-1"
    },
    r.symbol ? /* @__PURE__ */ React.createElement(
      Link,
      {
        to: `/security/${encodeURIComponent(r.symbol)}`,
        className: "min-w-0 truncate font-bold text-term-green hover:underline"
      },
      r.symbol,
      r.horizon_days ? ` \xB7 ${r.horizon_days}d` : ""
    ) : /* @__PURE__ */ React.createElement("span", { className: "min-w-0 truncate text-term-muted" }, "\u2014", r.horizon_days ? ` \xB7 ${r.horizon_days}d` : ""),
    /* @__PURE__ */ React.createElement("span", { className: "shrink-0 text-term-muted" }, typeof r.direction_probability === "number" ? `${(r.direction_probability * 100).toFixed(0)}%` : "\u2014")
  ))))));
}
export { HomePage as default };
