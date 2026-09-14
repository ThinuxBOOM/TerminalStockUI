import React, { Suspense, lazy, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { getAnalytics, getBars, getForecast, getQuote } from "../../api/client";
import ProvenanceBadge from "../../components/ProvenanceBadge";
import FreshnessBadge from "../../components/FreshnessBadge";
import MarketStateBadge from "../../components/MarketStateBadge";
import CurrencyValue from "../../components/CurrencyValue";
import Loading from "../../components/Loading";
import Skeleton from "../../components/Skeleton";
import ErrorState, { StaleBanner } from "../../components/ErrorState";
// PriceChart (and lightweight-charts) loads on demand — the brief header,
// forecast and events render without waiting for chart code.
const PriceChart = lazy(() => import("./PriceChart"));
const DISCLOSURE = "Not investment advice. For informational purposes only.";
function eventsFromAnalytics(a) {
  if (!a) return [];
  const raw = a.events;
  if (!Array.isArray(raw)) return [];
  const out = [];
  for (const e of raw) {
    if (typeof e === "string") {
      out.push({ date: "", title: e });
      continue;
    }
    if (e && typeof e === "object") {
      const r = e;
      const title = String(r.title ?? r.event ?? r.name ?? "").trim();
      if (!title) continue;
      const date = String(r.date ?? r.ts ?? r.as_of ?? "").trim();
      out.push({ date, title });
    }
  }
  return out.slice(0, 12);
}
function SecurityBrief({ symbol }) {
  const quote = useQuery({
    queryKey: ["quote", symbol],
    queryFn: ({ signal }) => getQuote(symbol, void 0, { signal }),
    retry: false,
    staleTime: 3e4
  });
  const forecastQ = useQuery({
    queryKey: ["forecast", symbol, 21],
    queryFn: ({ signal }) => getForecast(symbol, 21, { signal }),
    retry: false,
    staleTime: 6e4
  });
  const analyticsQ = useQuery({
    queryKey: ["analytics", symbol],
    queryFn: ({ signal }) => getAnalytics(symbol, { signal }),
    retry: false,
    staleTime: 6e4
  });
  const barsQ = useQuery({
    queryKey: ["bars", symbol],
    queryFn: ({ signal }) => getBars(symbol, "1d", 90, { signal }),
    retry: false,
    staleTime: 6e4
  });
  const forecast = forecastQ.data ?? null;
  const analytics = analyticsQ.data ?? null;
  const events = useMemo(() => eventsFromAnalytics(analytics), [analytics]);
  const q = quote.data;
  // Non-blocking quote: the header skeletons inline while forecast, chart
  // and events (already fetching in parallel) render from their own queries.
  const quoteLoading = quote.isLoading && !q;
  if (quoteLoading)
    return /* @__PURE__ */ React.createElement("div", { className: "max-w-full" }, /* @__PURE__ */ React.createElement(Skeleton, { label: `loading ${symbol}\u2026`, lines: 3 }), forecast && /* @__PURE__ */ React.createElement("div", { className: "term-panel mt-4 min-w-0 p-4" }, /* @__PURE__ */ React.createElement(ForecastCard, { price: null, currency: "USD", forecast })), /* @__PURE__ */ React.createElement("div", { className: "term-panel mt-4 min-w-0 p-4" }, /* @__PURE__ */ React.createElement("h3", { className: "term-label" }, "Price chart"), /* @__PURE__ */ React.createElement(Suspense, { fallback: /* @__PURE__ */ React.createElement(Skeleton, { label: "loading chart...", lines: 4 }) }, /* @__PURE__ */ React.createElement(PriceChart, { symbol, data: barsQ.data?.candles ?? null, loading: true, error: null, provenance: barsQ.data?.provenance ?? null }))), /* @__PURE__ */ React.createElement("p", { className: "mt-2 text-[11px] text-term-muted" }, DISCLOSURE));
  if (quote.isError) {
    // Hard error with no quote payload: ErrorState (with retry), not a
    // "cached data" banner — nothing is being shown.
    return /* @__PURE__ */ React.createElement("div", { className: "max-w-full" }, /* @__PURE__ */ React.createElement(
      ErrorState,
      {
        title: "Quote unavailable",
        detail: quote.error instanceof Error ? quote.error.message : "quote endpoint unreachable",
        onRetry: () => {
          void quote.refetch();
          void forecastQ.refetch();
        }
      }
    ), /* @__PURE__ */ React.createElement("p", { className: "mt-2 text-[11px] text-term-muted" }, DISCLOSURE));
  }
  if (!q)
    return /* @__PURE__ */ React.createElement("div", { className: "max-w-full" }, /* @__PURE__ */ React.createElement(
      ErrorState,
      {
        title: "Security Brief unavailable",
        detail: `No quote payload for ${symbol} \u2014 the quote endpoint returned empty.`,
        onRetry: () => {
          void quote.refetch();
          void forecastQ.refetch();
        }
      }
    ), /* @__PURE__ */ React.createElement("p", { className: "mt-2 text-[11px] text-term-muted" }, DISCLOSURE));
  const stale = q.provenance?.fallback_used === true || (q.provenance?.delay_minutes ?? 0) > 30;
  return /* @__PURE__ */ React.createElement("div", { className: "max-w-full" }, stale && /* @__PURE__ */ React.createElement(StaleBanner, { detail: `quote via ${q.provenance?.source ?? "unknown"}, delay ${q.provenance?.delay_minutes ?? "\u2014"}m` }), forecastQ.isError && /* @__PURE__ */ React.createElement(StaleBanner, { detail: `forecast endpoint unreachable (${forecastQ.error instanceof Error ? forecastQ.error.message : "unknown error"}) \u2014 forecast unavailable, no placeholder numbers shown` }), analyticsQ.isError && /* @__PURE__ */ React.createElement(StaleBanner, { detail: "analytics endpoint unreachable \u2014 snapshot shows unavailable, rest of the page unaffected" }), barsQ.isError && /* @__PURE__ */ React.createElement(StaleBanner, { detail: `price history unreachable (${barsQ.error instanceof Error ? barsQ.error.message : "bars endpoint error"}) \u2014 chart shows unavailable, rest of the page unaffected` }), /* @__PURE__ */ React.createElement("section", { className: "term-panel min-w-0 p-4", "aria-labelledby": "brief-forecast" }, /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center justify-between gap-2" }, /* @__PURE__ */ React.createElement("h2", { id: "brief-forecast", className: "min-w-0 text-lg font-bold" }, q.symbol, " ", /* @__PURE__ */ React.createElement("span", { className: "text-sm font-normal text-term-muted" }, /* @__PURE__ */ React.createElement(CurrencyValue, { value: q.price, currency: q.currency ?? "USD" }), typeof q.change_pct === "number" && Number.isFinite(q.change_pct) && /* @__PURE__ */ React.createElement("span", { className: q.change_pct >= 0 ? "text-term-green" : "text-term-red" }, " ", "(", q.change_pct >= 0 ? "+" : "", q.change_pct.toFixed(2), "%)"))), /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center gap-2" }, /* @__PURE__ */ React.createElement(MarketStateBadge, { state: q.market_state, provenance: q.provenance }), /* @__PURE__ */ React.createElement(FreshnessBadge, { p: q.provenance }))), q.ambiguous && /* @__PURE__ */ React.createElement(
    "div",
    {
      className: "mt-2 rounded border border-term-amber p-2 text-xs text-term-amber",
      role: "status"
    },
    "Ambiguous symbol \u2014 showing ",
    q.symbol,
    (q.candidates ?? []).length > 0 && `; other candidates: ${(q.candidates ?? []).join(", ")}`,
    ". Not auto-resolved; refine with the full provider symbol (e.g. 600519.SS)."
  ), /* @__PURE__ */ React.createElement("div", { className: "mt-1" }, /* @__PURE__ */ React.createElement(ProvenanceBadge, { p: q.provenance })), forecastQ.isLoading && /* @__PURE__ */ React.createElement("div", { className: "mt-3" }, /* @__PURE__ */ React.createElement(Skeleton, { label: "loading live forecast\u2026", lines: 4 })), !forecastQ.isLoading && forecast && /* @__PURE__ */ React.createElement(ForecastCard, { price: q.price, currency: q.currency ?? "USD", forecast }), !forecastQ.isLoading && !forecast && /* @__PURE__ */ React.createElement(
    ForecastUnavailable,
    {
      detail: "live forecast unreachable \u2014 no placeholder numbers shown",
      onRetry: () => void forecastQ.refetch()
    }
  )), /* @__PURE__ */ React.createElement("section", { className: "mt-4 grid max-w-full gap-4 md:grid-cols-2" }, /* @__PURE__ */ React.createElement("div", { className: "term-panel min-w-0 p-4", "aria-labelledby": "brief-chart" }, /* @__PURE__ */ React.createElement("h3", { id: "brief-chart", className: "term-label" }, "Price chart"), /* @__PURE__ */ React.createElement(Suspense, { fallback: /* @__PURE__ */ React.createElement(Skeleton, { label: "loading chart…", lines: 4 }) }, /* @__PURE__ */ React.createElement(
    PriceChart,
    {
      symbol,
      data: barsQ.data?.candles ?? null,
      loading: barsQ.isLoading || barsQ.isFetching,
      error: barsQ.isError ? barsQ.error instanceof Error ? barsQ.error.message : "bars endpoint unreachable" : null,
      provenance: barsQ.data?.provenance ?? null
    }
  ))), /* @__PURE__ */ React.createElement("div", { className: "term-panel min-w-0 p-4", "aria-labelledby": "brief-events" }, /* @__PURE__ */ React.createElement("h3", { id: "brief-events", className: "term-label" }, "Events timeline"), analyticsQ.isLoading && /* @__PURE__ */ React.createElement("div", { className: "mt-2" }, /* @__PURE__ */ React.createElement(Skeleton, { label: "loading events\u2026", lines: 3 })), !analyticsQ.isLoading && events.length > 0 && /* @__PURE__ */ React.createElement("ul", { className: "mt-2 space-y-2 text-sm" }, events.map((e, i) => /* @__PURE__ */ React.createElement(
    "li",
    {
      key: `${e.date}-${e.title}-${i}`,
      className: "flex justify-between gap-2 border-b border-term-border pb-1"
    },
    /* @__PURE__ */ React.createElement("span", { className: "min-w-0" }, e.title),
    /* @__PURE__ */ React.createElement("span", { className: "shrink-0 text-term-muted" }, e.date || "\u2014")
  ))), !analyticsQ.isLoading && events.length === 0 && /* @__PURE__ */ React.createElement("p", { className: "mt-2 text-xs text-term-muted", role: "status" }, "Events unavailable \u2014 no event feed for this symbol yet."), analytics && /* @__PURE__ */ React.createElement("div", { className: "mt-2" }, /* @__PURE__ */ React.createElement(ProvenanceBadge, { p: analytics.provenance })), /* @__PURE__ */ React.createElement(Link, { to: `/forecast/${encodeURIComponent(symbol)}`, className: "term-btn-ghost mt-3 inline-block text-xs" }, "FORECAST DETAILS \u2192"))), /* @__PURE__ */ React.createElement("section", { className: "mt-4" }, /* @__PURE__ */ React.createElement(AnalyticsSnapshot, { analytics, loading: analyticsQ.isLoading, failed: analyticsQ.isError })), /* @__PURE__ */ React.createElement("p", { className: "mt-3 text-[11px] text-term-muted" }, DISCLOSURE));
}
function ForecastUnavailable({ detail, onRetry }) {
  return /* @__PURE__ */ React.createElement("div", { className: "mt-3 rounded border border-term-border p-4", role: "status" }, /* @__PURE__ */ React.createElement("p", { className: "term-label" }, "Forecast \xB7 deterministic core (AI bounded, capped 20%)"), /* @__PURE__ */ React.createElement("p", { className: "mt-2 text-sm text-term-muted" }, "Forecast unavailable \u2014 ", detail, "."), onRetry && /* @__PURE__ */ React.createElement("button", { className: "term-btn-ghost mt-3 text-xs", type: "button", onClick: onRetry }, "RETRY FORECAST"));
}
function ForecastCard({
  price,
  currency,
  forecast: f
}) {
  const why = f.why ?? [];
  const risks = f.risks ?? [];
  const whyText = why.length > 0 ? why.join(" + ") : "unavailable";
  const riskText = risks.length > 0 ? risks.join(" + ") : "unavailable";
  return /* @__PURE__ */ React.createElement("div", { className: "mt-3 border-t border-term-border pt-3" }, /* @__PURE__ */ React.createElement("p", { className: "term-label" }, "Forecast \xB7 deterministic core (AI bounded, capped 20%)"), /* @__PURE__ */ React.createElement("div", { className: "mt-1 space-y-1 text-sm" }, /* @__PURE__ */ React.createElement("p", null, "Forecast:", " ", /* @__PURE__ */ React.createElement("b", { className: "text-term-text" }, f.label, ", ", f.horizon_days, " days"), " ", /* @__PURE__ */ React.createElement(FreshnessBadge, { p: f.provenance })), /* @__PURE__ */ React.createElement("p", { className: "text-2xl font-bold text-term-green" }, "Probability: ", (f.probability * 100).toFixed(0), "% ", /* @__PURE__ */ React.createElement(ProvenanceBadge, { p: f.provenance })), /* @__PURE__ */ React.createElement("p", { className: "text-xs" }, "Confidence: ", /* @__PURE__ */ React.createElement("b", null, f.confidence)), /* @__PURE__ */ React.createElement("p", { className: "text-xs" }, "Data quality: ", /* @__PURE__ */ React.createElement("b", { className: "text-term-cyan" }, f.quality_grade)), /* @__PURE__ */ React.createElement("p", { className: "text-xs" }, "AI provider:", " ", /* @__PURE__ */ React.createElement("b", null, f.provider && f.provider !== "deterministic-engine" ? f.provider : "none \u2014 deterministic core")), /* @__PURE__ */ React.createElement("p", { className: "text-xs" }, "Why: ", /* @__PURE__ */ React.createElement("span", { className: "text-term-muted" }, whyText)), /* @__PURE__ */ React.createElement("p", { className: "text-xs" }, "Risks: ", /* @__PURE__ */ React.createElement("span", { className: "text-term-muted" }, riskText))), /* @__PURE__ */ React.createElement("dl", { className: "mt-2 grid grid-cols-2 gap-2 text-xs md:grid-cols-4" }, /* @__PURE__ */ React.createElement("div", null, "Last price:", " ", /* @__PURE__ */ React.createElement("b", null, /* @__PURE__ */ React.createElement(CurrencyValue, { value: price, currency }))), /* @__PURE__ */ React.createElement("div", null, "Horizon: ", /* @__PURE__ */ React.createElement("b", null, f.horizon_days, "d"))), /* @__PURE__ */ React.createElement("div", { className: "mt-1" }, /* @__PURE__ */ React.createElement(ProvenanceBadge, { p: f.provenance })), /* @__PURE__ */ React.createElement("div", { className: "mt-2 grid gap-2 text-xs md:grid-cols-2" }, /* @__PURE__ */ React.createElement("div", { className: "rounded border border-term-border p-2" }, /* @__PURE__ */ React.createElement("p", { className: "font-bold text-term-green" }, "\u25B2 BULL \u2014 why"), why.length === 0 ? /* @__PURE__ */ React.createElement("p", { className: "text-term-muted" }, "unavailable") : /* @__PURE__ */ React.createElement("ul", { className: "list-disc pl-4 text-term-muted" }, why.map((b, i) => /* @__PURE__ */ React.createElement("li", { key: `${b}-${i}` }, b)))), /* @__PURE__ */ React.createElement("div", { className: "rounded border border-term-border p-2" }, /* @__PURE__ */ React.createElement("p", { className: "font-bold text-term-red" }, "\u25BC BEAR \u2014 risks"), risks.length === 0 ? /* @__PURE__ */ React.createElement("p", { className: "text-term-muted" }, "unavailable") : /* @__PURE__ */ React.createElement("ul", { className: "list-disc pl-4 text-term-muted" }, risks.map((b, i) => /* @__PURE__ */ React.createElement("li", { key: `${b}-${i}` }, b))))), /* @__PURE__ */ React.createElement("p", { className: "mt-2 text-[11px] text-term-muted" }, "Not investment advice."));
}
function AnalyticsSnapshot({
  analytics,
  loading,
  failed
}) {
  return /* @__PURE__ */ React.createElement("div", { className: "term-panel min-w-0 p-4", "aria-labelledby": "brief-analytics" }, /* @__PURE__ */ React.createElement("h3", { id: "brief-analytics", className: "term-label" }, "Analytics snapshot \xB7 deterministic"), analytics?.note && /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-[11px] text-term-amber", role: "note" }, analytics.note), loading && /* @__PURE__ */ React.createElement("div", { className: "mt-2" }, /* @__PURE__ */ React.createElement(Skeleton, { label: "loading analytics\u2026", lines: 4 })), failed && /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-xs text-term-amber", role: "alert" }, "\u26A0 analytics endpoint unreachable \u2014 snapshot unavailable, rest of the page unaffected."), !loading && !failed && !analytics && /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-xs text-term-muted", role: "status" }, "No analytics payload yet."), analytics && /* @__PURE__ */ React.createElement(React.Fragment, null, /* @__PURE__ */ React.createElement("dl", { className: "mt-2 grid grid-cols-2 gap-2 text-xs md:grid-cols-4" }, /* @__PURE__ */ React.createElement(SnapshotCell, { title: "Technical", data: analytics.technical }), /* @__PURE__ */ React.createElement(SnapshotCell, { title: "Fundamentals", data: analytics.fundamentals }), /* @__PURE__ */ React.createElement(SnapshotCell, { title: "Quality", data: analytics.quality }), /* @__PURE__ */ React.createElement(SnapshotCell, { title: "Valuation", data: analytics.valuation })), /* @__PURE__ */ React.createElement("div", { className: "mt-2 flex flex-wrap gap-2" }, /* @__PURE__ */ React.createElement(ProvenanceBadge, { p: analytics.provenance }), /* @__PURE__ */ React.createElement(FreshnessBadge, { p: analytics.provenance }))), !analytics && failed && /* @__PURE__ */ React.createElement("div", { className: "mt-2 grid grid-cols-2 gap-2 text-xs md:grid-cols-4" }, ["Technical", "Fundamentals", "Quality", "Valuation"].map((t) => /* @__PURE__ */ React.createElement("div", { key: t, className: "rounded border border-term-border p-2" }, /* @__PURE__ */ React.createElement("p", { className: "font-bold" }, t), /* @__PURE__ */ React.createElement("p", { className: "text-term-muted" }, "unavailable")))));
}
function SnapshotCell({ title, data }) {
  const all = Object.entries(data ?? {});
  const entries = all.slice(0, 4);
  return /* @__PURE__ */ React.createElement("div", { className: "min-w-0 rounded border border-term-border p-2" }, /* @__PURE__ */ React.createElement("p", { className: "font-bold" }, title), entries.length === 0 ? /* @__PURE__ */ React.createElement("p", { className: "text-term-muted" }, "unavailable") : /* @__PURE__ */ React.createElement("ul", { className: "mt-1 space-y-0.5 break-words text-term-muted" }, entries.map(([k, v]) => /* @__PURE__ */ React.createElement("li", { key: k }, k, ": ", /* @__PURE__ */ React.createElement("b", { className: "text-term-text" }, typeof v === "object" ? JSON.stringify(v) : String(v)))), all.length > entries.length && /* @__PURE__ */ React.createElement("li", { className: "text-[10px]", role: "status" }, "+", all.length - entries.length, " more")));
}
function SecurityBriefError({ onRetry }) {
  return /* @__PURE__ */ React.createElement(ErrorState, { title: "Security Brief failed", detail: "Quote + forecast both unreachable.", onRetry });
}
function SecurityBriefLoading({ symbol }) {
  return /* @__PURE__ */ React.createElement("div", null, /* @__PURE__ */ React.createElement(Loading, { label: `loading ${symbol}\u2026` }), /* @__PURE__ */ React.createElement("p", { className: "mt-2 text-[11px] text-term-muted" }, "Not investment advice."));
}
export { SecurityBriefError, SecurityBriefLoading, SecurityBrief as default };
