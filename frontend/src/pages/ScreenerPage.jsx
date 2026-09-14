import React, { useEffect, useMemo, useState } from "react";
import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { FORECAST_HORIZONS, getScreener } from "../api/client";
import CurrencyValue from "../components/CurrencyValue";
import EmptyState from "../components/EmptyState";
import ErrorState, { StaleBanner } from "../components/ErrorState";
import MarketStateBadge from "../components/MarketStateBadge";
import ProvenanceBadge from "../components/ProvenanceBadge";
import Skeleton from "../components/Skeleton";
const MARKET_OPTIONS = [
  { label: "All", value: "" },
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
  const flag = typeof q.quality_flag === "string" && q.quality_flag ? q.quality_flag : "\u2014";
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
    return "Full-universe scan timed out (takes ~30s cold: 17 quotes + forecasts). Retry \u2014 warm quotes/cache make repeats faster.";
  }
  return message;
}
function ScreenerPage() {
  const [market, setMarket] = useState("");
  const [horizon, setHorizon] = useState(21);
  const SCREENER_LIMIT = 20;
  const [offset, setOffset] = useState(0);
  // Slider input stays responsive while the backend query fires on a
  // debounced value: a full-universe scan takes ~30s cold, so every 0.01
  // tick must not refetch.
  const [minProbInput, setMinProbInput] = useState(0.5);
  const [minProb, setMinProb] = useState(0.5);
  useEffect(() => {
    const t = setTimeout(() => setMinProb(clampProb(minProbInput)), 450);
    return () => clearTimeout(t);
  }, [minProbInput]);
  // Backend paginates filtered rows: any filter change restarts at page 0.
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
    staleTime: 3e4,
    retry: false,
    // Keep the previous result set on slider commits instead of
    // flashing back to the skeleton.
    placeholderData: keepPreviousData
  });
  const data = screen.data;
  const rows = useMemo(() => data?.results ?? [], [data]);
  const skippedCount = data?.skipped?.length ?? 0;
  const skippedSymbols = useMemo(
    () => (data?.skipped ?? []).slice(0, MAX_SKIPPED_SHOWN).map((s) => s.symbol),
    [data]
  );
  const skippedOverflow = skippedCount > skippedSymbols.length;
  const anyFallback = useMemo(
    () => rows.some((r) => r.provenance?.fallback_used === true),
    [rows]
  );
  return /* @__PURE__ */ React.createElement("div", null, /* @__PURE__ */ React.createElement("h1", { className: "mb-3 text-sm tracking-widest text-term-muted" }, "SCREENER \xB7 RANKED BY FORECAST DIRECTION"), /* @__PURE__ */ React.createElement("section", { className: "term-panel p-4" }, /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-end gap-3" }, /* @__PURE__ */ React.createElement("div", null, /* @__PURE__ */ React.createElement("label", { className: "term-label", htmlFor: "screener-market" }, "Market"), /* @__PURE__ */ React.createElement(
    "select",
    {
      id: "screener-market",
      className: "term-input mt-1",
      value: market,
      onChange: (e) => setMarket(e.target.value),
      "aria-label": "Filter screener by market"
    },
    MARKET_OPTIONS.map((m) => /* @__PURE__ */ React.createElement("option", { key: m.label, value: m.value }, m.label))
  )), /* @__PURE__ */ React.createElement("div", null, /* @__PURE__ */ React.createElement("p", { className: "term-label", id: "screener-horizon-label" }, "Horizon (trading days)"), /* @__PURE__ */ React.createElement(
    "div",
    {
      className: "mt-1 flex gap-1",
      role: "group",
      "aria-labelledby": "screener-horizon-label"
    },
    FORECAST_HORIZONS.map((h) => /* @__PURE__ */ React.createElement(
      "button",
      {
        key: h,
        type: "button",
        className: h === horizon ? "term-btn" : "term-btn-ghost",
        "aria-pressed": h === horizon,
        onClick: () => setHorizon(h)
      },
      h,
      "d"
    ))
  )), /* @__PURE__ */ React.createElement("div", null, /* @__PURE__ */ React.createElement("label", { className: "term-label", htmlFor: "screener-min-prob" }, "Min probability ", (minProbInput * 100).toFixed(0), "%"), /* @__PURE__ */ React.createElement("div", { className: "mt-1 flex items-center gap-2" }, /* @__PURE__ */ React.createElement(
    "input",
    {
      id: "screener-min-prob",
      type: "range",
      min: 0,
      max: 1,
      step: 0.01,
      value: minProbInput,
      onChange: (e) => setMinProbInput(clampProb(Number(e.target.value))),
      "aria-label": "Minimum direction probability (slider)",
      className: "w-full min-w-32 max-w-52"
    }
  ), /* @__PURE__ */ React.createElement(
    "input",
    {
      type: "number",
      min: 0,
      max: 1,
      step: 0.01,
      value: minProbInput,
      onChange: (e) => setMinProbInput(clampProb(Number(e.target.value))),
      "aria-label": "Minimum direction probability (numeric)",
      className: "term-input w-20"
    }
  )))), /* @__PURE__ */ React.createElement("p", { className: "mt-2 text-[11px] text-term-muted" }, "Deterministic ensemble forecast at ", horizon, "d \xB7 ranked by direction probability desc \xB7 seed universe only.")), /* @__PURE__ */ React.createElement("div", { className: "mt-4" }, screen.isLoading && /* @__PURE__ */ React.createElement(Skeleton, { label: "scanning universe\u2026", lines: 6 }), screen.isError && /* @__PURE__ */ React.createElement(
    ErrorState,
    {
      title: "Screener unavailable",
      detail: screenerErrorDetail(screen.error),
      onRetry: () => void screen.refetch()
    }
  ), !screen.isLoading && !screen.isError && data && /* @__PURE__ */ React.createElement("div", { className: "space-y-3" }, anyFallback && /* @__PURE__ */ React.createElement(
    StaleBanner,
    {
      detail: "screener rows include fallback data \u2014 prices/probabilities are stale-marked, not live"
    }
  ), /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center justify-between gap-2" }, /* @__PURE__ */ React.createElement("p", { className: "text-xs text-term-muted", role: "status" }, data.count, " of ", data.filtered_total ?? data.universe_size, " pass \xB7 page ", Math.floor((data.offset ?? offset) / SCREENER_LIMIT) + 1, skippedCount > 0 && ` \xB7 ${skippedCount} skipped (see below)`), /* @__PURE__ */ React.createElement("div", { className: "flex items-center gap-2" }, /* @__PURE__ */ React.createElement(
    "button",
    {
      className: "term-btn-ghost text-xs",
      type: "button",
      disabled: (data.offset ?? offset) <= 0 || screen.isFetching,
      onClick: () => setOffset((o) => Math.max(0, o - SCREENER_LIMIT)),
      "aria-label": "Previous screener page"
    },
    "\u2190 PREV"
  ), /* @__PURE__ */ React.createElement(
    "button",
    {
      className: "term-btn-ghost text-xs",
      type: "button",
      disabled: (data.offset ?? offset) + data.count >= (data.filtered_total ?? data.count) || screen.isFetching,
      onClick: () => setOffset((o) => o + SCREENER_LIMIT),
      "aria-label": "Next screener page"
    },
    "NEXT \u2192"
  ))), rows.length === 0 ? /* @__PURE__ */ React.createElement(
    EmptyState,
    {
      title: "No instruments pass the screen",
      detail: "Lower the minimum probability or widen the market filter.",
      actionLabel: "Reset filters",
      onAction: () => {
        setMarket("");
        setHorizon(21);
        setMinProbInput(0.5);
        setMinProb(0.5);
        setOffset(0);
      }
    }
  ) : /* @__PURE__ */ React.createElement("div", { className: "term-panel overflow-x-auto" }, /* @__PURE__ */ React.createElement("table", { className: "w-full text-sm" }, /* @__PURE__ */ React.createElement("caption", { className: "sr-only" }, "Screener results ranked by forecast direction"), /* @__PURE__ */ React.createElement("thead", null, /* @__PURE__ */ React.createElement("tr", { className: "border-b border-term-border text-left text-xs text-term-muted" }, /* @__PURE__ */ React.createElement("th", { scope: "col", className: "p-2" }, "#"), /* @__PURE__ */ React.createElement("th", { scope: "col", className: "p-2" }, "Symbol"), /* @__PURE__ */ React.createElement("th", { scope: "col", className: "p-2" }, "Price"), /* @__PURE__ */ React.createElement("th", { scope: "col", className: "p-2" }, "Prob ", horizon, "d"), /* @__PURE__ */ React.createElement("th", { scope: "col", className: "p-2" }, "Confidence"), /* @__PURE__ */ React.createElement("th", { scope: "col", className: "p-2" }, "Quality"), /* @__PURE__ */ React.createElement("th", { scope: "col", className: "p-2" }, "Market"), /* @__PURE__ */ React.createElement("th", { scope: "col", className: "p-2" }, "Provenance"))), /* @__PURE__ */ React.createElement("tbody", null, rows.map((r, idx) => /* @__PURE__ */ React.createElement("tr", { key: `${r.symbol}-${r.exchange_mic}-${idx}`, className: "border-b border-term-border" }, /* @__PURE__ */ React.createElement("td", { className: "p-2 text-term-muted" }, (data.offset ?? offset) + idx + 1), /* @__PURE__ */ React.createElement("td", { className: "p-2" }, /* @__PURE__ */ React.createElement(
    Link,
    {
      to: `/security/${encodeURIComponent(r.symbol)}`,
      className: "font-bold text-term-green hover:underline"
    },
    r.symbol
  ), /* @__PURE__ */ React.createElement("span", { className: "ml-2 text-xs text-term-muted" }, r.company_name, r.exchange_mic ? ` \xB7 ${r.exchange_mic}` : "")), /* @__PURE__ */ React.createElement("td", { className: "p-2" }, /* @__PURE__ */ React.createElement(CurrencyValue, { value: r.price, currency: r.currency })), /* @__PURE__ */ React.createElement("td", { className: "p-2" }, /* @__PURE__ */ React.createElement("b", null, Number.isFinite(r.direction_probability) ? `${(r.direction_probability * 100).toFixed(1)}%` : "\u2014")), /* @__PURE__ */ React.createElement("td", { className: "p-2 text-xs" }, r.confidence), /* @__PURE__ */ React.createElement(
    "td",
    {
      className: "p-2 text-xs text-term-muted",
      title: qualityReason(r)
    },
    qualityFlag(r)
  ), /* @__PURE__ */ React.createElement("td", { className: "p-2" }, /* @__PURE__ */ React.createElement(MarketStateBadge, { state: r.market_state, provenance: r.provenance })), /* @__PURE__ */ React.createElement("td", { className: "p-2" }, /* @__PURE__ */ React.createElement(ProvenanceBadge, { p: r.provenance })))))), /* @__PURE__ */ React.createElement("p", { className: "p-2 text-[11px] text-term-muted" }, data.disclosure || "Not investment advice.", skippedCount > 0 && /* @__PURE__ */ React.createElement("span", { className: "ml-2" }, "Skipped: ", skippedSymbols.join(", "), skippedOverflow ? ` +${skippedCount - skippedSymbols.length} more` : "", "."))))));
}
export { ScreenerPage as default };
