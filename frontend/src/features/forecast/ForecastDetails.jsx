import React, { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { AI_PROFILES, FORECAST_HORIZONS, friendlyAIError, getAnalytics, getForecast, postAIInsight } from "../../api/client";
import { getCalibrationHistory } from "../../api/calibrationHistory";
import { getRecentBacktests } from "../../api/backtestHistory";
import ProvenanceBadge from "../../components/ProvenanceBadge";
import FreshnessBadge from "../../components/FreshnessBadge";
import CalibrationChart from "../../components/CalibrationChart";
import AIOpinionCard from "../../components/AIOpinionCard";
import Loading from "../../components/Loading";
import { StaleBanner } from "../../components/ErrorState";
const DISCLOSURE = "Not investment advice. Forecasts are measurable probabilities from the deterministic engine; AI opinions are bounded and capped at 20% influence.";
const MAX_CAL_TABLE_ROWS = 20;
const MAX_CAL_TREND_ROWS = 10;
const MAX_ANALYTICS_CELLS = 12;
function fmtPct(p) {
  return `${(p * 100).toFixed(1)}%`;
}
function ForecastDetails({ symbol }) {
  const [horizon, setHorizon] = useState(21);
  const [aiProfile, setAiProfile] = useState("Forecast Assist");
  const forecastQ = useQuery({
    queryKey: ["forecast", symbol, horizon],
    queryFn: ({ signal }) => getForecast(symbol, horizon, { signal }),
    retry: false,
    staleTime: 6e4
  });
  const analyticsQ = useQuery({
    queryKey: ["analytics", symbol],
    queryFn: ({ signal }) => getAnalytics(symbol, { signal }),
    retry: false,
    staleTime: 6e4
  });
  const aiM = useMutation({
    mutationFn: () => postAIInsight(symbol, aiProfile)
  });
  const live = forecastQ.data ?? null;
  const f = live;
  const analytics = analyticsQ.data ?? null;
  const calHistoryQ = useQuery({
    queryKey: ["calibration-history", symbol, horizon],
    queryFn: () => getCalibrationHistory(symbol, horizon, 20),
    retry: false,
    staleTime: 6e4
  });
  const calHistory = calHistoryQ.data ?? [];
  const latestMeta = calHistory[0] ?? null;
  const liveBins = f?.calibration ?? [];
  const chartRows = useMemo(
    () => liveBins.length > 0 ? liveBins : latestMeta?.reliability ?? [],
    [liveBins, latestMeta]
  );
  const hasAnyCalibration = chartRows.length > 0 || calHistory.length > 0;
  const normalizedSymbol = symbol.trim().toUpperCase();
  const recentBacktest = useMemo(
    // Match the viewed horizon: a 5d run must not vouch for the 63d tab.
    () => getRecentBacktests().find(
      (r) => r.symbol === normalizedSymbol && (r.horizons.length === 0 || r.horizons.includes(horizon))
    ),
    [normalizedSymbol, horizon, forecastQ.dataUpdatedAt]
  );
  const versions = f?.versions ?? {};
  const inputs = f?.inputs ?? {};
  const metaBrier = latestMeta?.brier ?? null;
  const metaEce = latestMeta?.ece ?? null;
  const metaN = latestMeta?.n_windows ?? (typeof inputs.n_windows === "number" ? inputs.n_windows : null);
  const metaModel = latestMeta?.model_version ?? (typeof versions.model_version === "string" ? versions.model_version : null) ?? (typeof inputs.model_version === "string" ? inputs.model_version : null);
  const metaData = latestMeta?.data_version ?? (typeof versions.data_version === "string" ? versions.data_version : null) ?? (typeof inputs.data_version === "string" ? inputs.data_version : null);
  const dirWord = f ? f.probability >= 0.5 ? "bullish" : "bearish" : void 0;
  return /* @__PURE__ */ React.createElement("div", { className: "space-y-4" }, /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center gap-2 text-xs" }, /* @__PURE__ */ React.createElement("span", { className: "term-label" }, "Horizon (trading days)"), FORECAST_HORIZONS.map((h) => /* @__PURE__ */ React.createElement(
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
  )), /* @__PURE__ */ React.createElement("span", { className: "text-term-muted" }, "targets: direction probability \xB7 return range \xB7 vol regime \xB7 drawdown")), forecastQ.isLoading && /* @__PURE__ */ React.createElement(Loading, { label: `loading forecast ${symbol} ${horizon}d\u2026` }), forecastQ.isError && /* @__PURE__ */ React.createElement(
    StaleBanner,
    {
      detail: `forecast endpoint unreachable (${forecastQ.error instanceof Error ? forecastQ.error.message : "unknown error"}) \u2014 forecast unavailable, no placeholder numbers shown`
    }
  ), !forecastQ.isError && !forecastQ.isLoading && !live && /* @__PURE__ */ React.createElement(StaleBanner, { detail: "live forecast not yet returned \u2014 forecast unavailable, no placeholder numbers shown" }), f && (f.provenance.fallback_used || f.provenance.delay_minutes > 30) && !forecastQ.isError && /* @__PURE__ */ React.createElement(
    StaleBanner,
    {
      detail: `forecast via ${f.provenance.source}, delay ${f.provenance.delay_minutes}m${f.provenance.fallback_used ? " \u2014 fallback/synthetic bars, not market history" : ""}`
    }
  ), !f && !forecastQ.isLoading && /* @__PURE__ */ React.createElement("section", { className: "term-panel p-4", role: "status" }, /* @__PURE__ */ React.createElement("p", { className: "term-label" }, "Forecast \xB7 deterministic engine"), /* @__PURE__ */ React.createElement("p", { className: "mt-2 text-sm text-term-muted" }, "Forecast unavailable for ", symbol, " at ", horizon, "d \u2014 the forecast endpoint is unreachable or returned no data. No placeholder numbers are shown."), /* @__PURE__ */ React.createElement(
    "button",
    {
      className: "term-btn-ghost mt-3 text-xs",
      type: "button",
      onClick: () => void forecastQ.refetch()
    },
    "RETRY FORECAST"
  )), f && /* @__PURE__ */ React.createElement("section", { className: "term-panel p-4" }, /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center justify-between gap-2" }, /* @__PURE__ */ React.createElement("h2", { className: "text-lg font-bold" }, "Forecast: ", f.label, ", ", f.horizon_days, " days", " ", /* @__PURE__ */ React.createElement(FreshnessBadge, { p: f.provenance }))), /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-2xl font-bold text-term-green" }, fmtPct(f.probability), " ", /* @__PURE__ */ React.createElement(ProvenanceBadge, { p: f.provenance })), /* @__PURE__ */ React.createElement("dl", { className: "mt-2 grid grid-cols-2 gap-2 text-xs md:grid-cols-4" }, /* @__PURE__ */ React.createElement("div", null, "Confidence: ", /* @__PURE__ */ React.createElement("b", null, f.confidence), " ", /* @__PURE__ */ React.createElement(ProvenanceBadge, { p: f.provenance })), /* @__PURE__ */ React.createElement("div", null, "Data quality: ", /* @__PURE__ */ React.createElement("b", { className: "text-term-cyan" }, f.quality_grade), " ", /* @__PURE__ */ React.createElement(ProvenanceBadge, { p: f.provenance })), /* @__PURE__ */ React.createElement("div", null, "Provider: ", /* @__PURE__ */ React.createElement("b", null, f.provider)), /* @__PURE__ */ React.createElement("div", null, "Horizon: ", /* @__PURE__ */ React.createElement("b", null, f.horizon_days, "d"))), /* @__PURE__ */ React.createElement("div", { className: "mt-2 grid gap-2 text-xs md:grid-cols-2" }, /* @__PURE__ */ React.createElement("div", { className: "rounded border border-term-border p-2" }, /* @__PURE__ */ React.createElement("p", { className: "font-bold text-term-green" }, "Why (bullish drivers)"), (f.why ?? []).length === 0 ? /* @__PURE__ */ React.createElement("p", { className: "text-term-muted" }, "unavailable") : /* @__PURE__ */ React.createElement("ul", { className: "list-disc pl-4 text-term-muted" }, (f.why ?? []).map((w, i) => /* @__PURE__ */ React.createElement("li", { key: `${w}-${i}` }, w)))), /* @__PURE__ */ React.createElement("div", { className: "rounded border border-term-border p-2" }, /* @__PURE__ */ React.createElement("p", { className: "font-bold text-term-red" }, "Risks (bearish drivers)"), (f.risks ?? []).length === 0 ? /* @__PURE__ */ React.createElement("p", { className: "text-term-muted" }, "unavailable") : /* @__PURE__ */ React.createElement("ul", { className: "list-disc pl-4 text-term-muted" }, (f.risks ?? []).map((w, i) => /* @__PURE__ */ React.createElement("li", { key: `${w}-${i}` }, w)))))), f && /* @__PURE__ */ React.createElement("section", { className: "term-panel p-4" }, /* @__PURE__ */ React.createElement("p", { className: "term-label" }, "Inputs \xB7 evidence \xB7 versions"), /* @__PURE__ */ React.createElement("div", { className: "mt-2 grid gap-2 text-xs md:grid-cols-3" }, /* @__PURE__ */ React.createElement("div", { className: "rounded border border-term-border p-2" }, /* @__PURE__ */ React.createElement("p", { className: "font-bold" }, "Model versions"), /* @__PURE__ */ React.createElement("ul", { className: "mt-1 space-y-0.5 text-term-muted" }, f.versions ? Object.entries(f.versions).map(([k, v]) => /* @__PURE__ */ React.createElement("li", { key: k }, k, ": ", /* @__PURE__ */ React.createElement("b", { className: "text-term-text" }, String(v)))) : /* @__PURE__ */ React.createElement("li", null, "unavailable"))), /* @__PURE__ */ React.createElement("div", { className: "rounded border border-term-border p-2" }, /* @__PURE__ */ React.createElement("p", { className: "font-bold" }, "Evidence IDs"), (f.evidence_ids ?? []).length === 0 ? /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-term-muted" }, "none") : /* @__PURE__ */ React.createElement("p", { className: "mt-1" }, (f.evidence_ids ?? []).slice(0, 24).map((e, i) => /* @__PURE__ */ React.createElement("code", { key: `${e}-${i}`, className: "mr-1 rounded bg-term-bg px-1 py-0.5 text-term-cyan" }, e)), (f.evidence_ids ?? []).length > 24 && /* @__PURE__ */ React.createElement("span", { className: "text-term-muted" }, "\u2026 +", (f.evidence_ids ?? []).length - 24, " more")), f.inputs && /* @__PURE__ */ React.createElement("ul", { className: "mt-2 space-y-0.5 text-term-muted" }, Object.entries(f.inputs).slice(0, 12).map(([k, v]) => /* @__PURE__ */ React.createElement("li", { key: k }, k, ": ", /* @__PURE__ */ React.createElement("span", { className: "text-term-text" }, String(v)))))), /* @__PURE__ */ React.createElement("div", { className: "rounded border border-term-border p-2" }, /* @__PURE__ */ React.createElement("p", { className: "font-bold" }, "Intervals (expected return)"), f.intervals && typeof f.intervals.low === "number" && typeof f.intervals.mid === "number" && typeof f.intervals.high === "number" && Number.isFinite(f.intervals.low) && Number.isFinite(f.intervals.mid) && Number.isFinite(f.intervals.high) ? /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-term-muted" }, "low ", /* @__PURE__ */ React.createElement("b", { className: "text-term-red" }, (f.intervals.low * 100).toFixed(1), "%"), " \xB7 mid", " ", /* @__PURE__ */ React.createElement("b", { className: "text-term-text" }, (f.intervals.mid * 100).toFixed(1), "%"), " \xB7 high", " ", /* @__PURE__ */ React.createElement("b", { className: "text-term-green" }, (f.intervals.high * 100).toFixed(1), "%"), " ", /* @__PURE__ */ React.createElement(ProvenanceBadge, { p: f.provenance })) : /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-term-muted" }, "unavailable"))), /* @__PURE__ */ React.createElement("div", { className: "mt-2" }, /* @__PURE__ */ React.createElement(ProvenanceBadge, { p: f.provenance }))), /* @__PURE__ */ React.createElement("section", { className: "term-panel p-4" }, /* @__PURE__ */ React.createElement("div", { className: "mb-2 grid grid-cols-2 gap-2 text-xs md:grid-cols-5" }, /* @__PURE__ */ React.createElement("div", { className: "rounded border border-term-border p-2" }, /* @__PURE__ */ React.createElement("p", { className: "text-term-muted" }, "Brier"), /* @__PURE__ */ React.createElement("p", { className: "text-base font-bold" }, metaBrier === null || metaBrier === void 0 ? "\u2014" : Number(metaBrier).toFixed(4))), /* @__PURE__ */ React.createElement("div", { className: "rounded border border-term-border p-2" }, /* @__PURE__ */ React.createElement("p", { className: "text-term-muted" }, "ECE"), /* @__PURE__ */ React.createElement("p", { className: "text-base font-bold" }, metaEce === null || metaEce === void 0 ? "\u2014" : Number(metaEce).toFixed(4))), /* @__PURE__ */ React.createElement("div", { className: "rounded border border-term-border p-2" }, /* @__PURE__ */ React.createElement("p", { className: "text-term-muted" }, "Windows"), /* @__PURE__ */ React.createElement("p", { className: "text-base font-bold" }, metaN ?? "\u2014")), /* @__PURE__ */ React.createElement("div", { className: "rounded border border-term-border p-2" }, /* @__PURE__ */ React.createElement("p", { className: "text-term-muted" }, "Model"), /* @__PURE__ */ React.createElement("p", { className: "truncate text-xs font-bold", title: String(metaModel ?? "") }, metaModel ? String(metaModel) : "\u2014")), /* @__PURE__ */ React.createElement("div", { className: "rounded border border-term-border p-2" }, /* @__PURE__ */ React.createElement("p", { className: "text-term-muted" }, "Data"), /* @__PURE__ */ React.createElement("p", { className: "truncate text-xs font-bold", title: String(metaData ?? "") }, metaData ? String(metaData) : "\u2014"))), latestMeta ? /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-[10px] text-term-muted" }, "Brier/ECE above are from the persisted calibration snapshot", latestMeta.created_at ? ` (${new Date(latestMeta.created_at).toLocaleString()})` : "", latestMeta.model_version ? ` \xB7 model ${latestMeta.model_version}` : "", " \u2014 not computed from the live forecast above.") : /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-[10px] text-term-muted" }, "No persisted calibration snapshot yet \u2014 Brier/ECE unavailable."), /* @__PURE__ */ React.createElement(CalibrationChart, { rows: chartRows, title: `Calibration history \xB7 ${f?.horizon_days ?? horizon}d` }), chartRows.length > 0 ? /* @__PURE__ */ React.createElement("div", { className: "mt-2 overflow-x-auto" }, chartRows.length > MAX_CAL_TABLE_ROWS && /* @__PURE__ */ React.createElement("p", { className: "mb-1 text-[11px] text-term-muted", role: "status" }, "showing first ", MAX_CAL_TABLE_ROWS, " of ", chartRows.length, " bins."), /* @__PURE__ */ React.createElement("table", { className: "w-full text-xs" }, /* @__PURE__ */ React.createElement("thead", null, /* @__PURE__ */ React.createElement("tr", { className: "text-left text-term-muted" }, /* @__PURE__ */ React.createElement("th", { className: "py-1 pr-2" }, "Bin"), /* @__PURE__ */ React.createElement("th", { className: "py-1 pr-2" }, "n"), /* @__PURE__ */ React.createElement("th", { className: "py-1 pr-2" }, "Mean predicted"), /* @__PURE__ */ React.createElement("th", { className: "py-1 pr-2" }, "Fraction positive"))), /* @__PURE__ */ React.createElement("tbody", null, chartRows.slice(0, MAX_CAL_TABLE_ROWS).map((r, i) => /* @__PURE__ */ React.createElement("tr", { key: `${r.bin_low}-${r.bin_high}-${i}`, className: "border-t border-term-border" }, /* @__PURE__ */ React.createElement("td", { className: "py-1 pr-2" }, typeof r.bin_low === "number" && Number.isFinite(r.bin_low) ? r.bin_low.toFixed(2) : "\u2014", "\u2013", typeof r.bin_high === "number" && Number.isFinite(r.bin_high) ? r.bin_high.toFixed(2) : "\u2014"), /* @__PURE__ */ React.createElement("td", { className: "py-1 pr-2" }, r.count), /* @__PURE__ */ React.createElement("td", { className: "py-1 pr-2" }, typeof r.mean_predicted === "number" && Number.isFinite(r.mean_predicted) ? r.mean_predicted.toFixed(3) : "\u2014"), /* @__PURE__ */ React.createElement("td", { className: "py-1 pr-2" }, typeof r.fraction_positive === "number" && Number.isFinite(r.fraction_positive) ? r.fraction_positive.toFixed(3) : "\u2014"))))), liveBins.length === 0 && latestMeta && /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-[10px] text-term-muted" }, "Live bins unavailable \u2014 showing latest persisted snapshot", latestMeta.created_at ? ` (${latestMeta.created_at})` : "", ".")) : null, !hasAnyCalibration && /* @__PURE__ */ React.createElement("p", { className: "mt-2 text-xs text-term-muted" }, "No calibration bins yet \u2014 walk-forward history lands with the M3 engine; the Backtest Lab shows failures as well as successes."), calHistoryQ.isLoading && /* @__PURE__ */ React.createElement("p", { className: "mt-2 text-[11px] text-term-muted", role: "status" }, "loading calibration history\u2026"), calHistoryQ.isError && /* @__PURE__ */ React.createElement("p", { className: "mt-2 text-[11px] text-term-amber", role: "alert" }, "\u26A0 calibration history unavailable (", calHistoryQ.error instanceof Error ? calHistoryQ.error.message : "backend unreachable", ") \u2014 live bins above unaffected."), calHistory.length > 0 && /* @__PURE__ */ React.createElement("div", { className: "mt-3" }, /* @__PURE__ */ React.createElement("p", { className: "term-label" }, "Calibration trend \xB7 past snapshots"), calHistory.length > MAX_CAL_TREND_ROWS && /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-[11px] text-term-muted", role: "status" }, "showing first ", MAX_CAL_TREND_ROWS, " of ", calHistory.length, " snapshots."), /* @__PURE__ */ React.createElement("div", { className: "mt-1 space-y-1" }, calHistory.slice(0, MAX_CAL_TREND_ROWS).map((h, i) => {
    const b = h.brier ?? 0;
    const e = h.ece ?? 0;
    const bWidth = Math.min(100, Math.max(0, b / 0.25 * 100));
    const eWidth = Math.min(100, Math.max(0, e / 0.2 * 100));
    return /* @__PURE__ */ React.createElement("div", { key: `${h.created_at ?? "snapshot"}-${i}`, className: "text-[11px]" }, /* @__PURE__ */ React.createElement("div", { className: "flex justify-between gap-2 text-term-muted" }, /* @__PURE__ */ React.createElement("span", null, h.created_at ? new Date(h.created_at).toLocaleString() : `snapshot ${i + 1}`), /* @__PURE__ */ React.createElement("span", null, "Brier ", h.brier === null ? "\u2014" : Number(h.brier).toFixed(4), " \xB7 ECE", " ", h.ece === null ? "\u2014" : Number(h.ece).toFixed(4), h.n_windows !== null ? ` \xB7 n=${h.n_windows}` : "")), /* @__PURE__ */ React.createElement("div", { className: "mt-0.5 h-1 w-full rounded bg-term-border" }, /* @__PURE__ */ React.createElement(
      "div",
      {
        className: "h-1 rounded bg-term-green",
        style: { width: `${bWidth}%` },
        title: `Brier ${h.brier ?? "\u2014"} (0 = perfect, 0.25 = coin-flip)`
      }
    )), /* @__PURE__ */ React.createElement("div", { className: "mt-0.5 h-1 w-full rounded bg-term-border" }, /* @__PURE__ */ React.createElement(
      "div",
      {
        className: "h-1 rounded bg-term-cyan",
        style: { width: `${eWidth}%` },
        title: `ECE ${h.ece ?? "\u2014"} (lower = better calibrated)`
      }
    )));
  }))), /* @__PURE__ */ React.createElement("div", { className: "mt-2 flex flex-wrap items-center gap-2 text-xs" }, /* @__PURE__ */ React.createElement(
    Link,
    {
      className: "term-btn-ghost text-xs",
      to: `/backtest?symbol=${encodeURIComponent(symbol)}`
    },
    "RUN BACKTEST \u2192"
  ), recentBacktest && /* @__PURE__ */ React.createElement("span", { className: "text-[11px] text-term-muted" }, "Backtest run ", new Date(recentBacktest.at).toLocaleString(), recentBacktest.horizons.length > 0 ? ` (${recentBacktest.horizons.map((x) => `${x}d`).join(", ")})` : "", "\u2014 see the Backtest Lab for details.")), f && /* @__PURE__ */ React.createElement("div", { className: "mt-2" }, /* @__PURE__ */ React.createElement(ProvenanceBadge, { p: f.provenance }))), /* @__PURE__ */ React.createElement("section", { className: "term-panel p-4" }, /* @__PURE__ */ React.createElement("p", { className: "term-label" }, "Deterministic analytics snapshot"), analytics?.note && /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-[11px] text-term-amber", role: "note" }, analytics.note), analyticsQ.isLoading && /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-xs text-term-muted" }, "loading analytics\u2026"), analyticsQ.isError && /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-xs text-term-amber" }, "\u26A0 analytics endpoint unreachable \u2014 snapshot unavailable, forecast above unaffected."), analytics && /* @__PURE__ */ React.createElement(React.Fragment, null, /* @__PURE__ */ React.createElement(AnalyticsGrid, { title: "Technical", data: analytics.technical }), /* @__PURE__ */ React.createElement(AnalyticsGrid, { title: "Fundamentals", data: analytics.fundamentals }), /* @__PURE__ */ React.createElement(AnalyticsGrid, { title: "Quality", data: analytics.quality }), /* @__PURE__ */ React.createElement(AnalyticsGrid, { title: "Valuation", data: analytics.valuation }), /* @__PURE__ */ React.createElement("div", { className: "mt-2 flex flex-wrap gap-2" }, /* @__PURE__ */ React.createElement(ProvenanceBadge, { p: analytics.provenance }), /* @__PURE__ */ React.createElement(FreshnessBadge, { p: analytics.provenance }))), !analytics && !analyticsQ.isLoading && !analyticsQ.isError && /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-xs text-term-muted" }, "No analytics payload yet.")), /* @__PURE__ */ React.createElement("section", { className: "term-panel p-4" }, /* @__PURE__ */ React.createElement("p", { className: "term-label" }, "Limitations"), f && (f.limitations ?? []).length > 0 ? /* @__PURE__ */ React.createElement("ul", { className: "mt-1 list-disc pl-5 text-sm text-term-muted" }, (f.limitations ?? []).map((l, i) => /* @__PURE__ */ React.createElement("li", { key: `${l}-${i}` }, l))) : /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-sm text-term-muted" }, "unavailable \u2014 live forecast not loaded."), /* @__PURE__ */ React.createElement("div", { className: "mt-3 rounded border border-term-amber bg-term-panel p-3 text-xs text-term-amber" }, f?.disclosure ?? DISCLOSURE)), /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center gap-2 text-xs" }, /* @__PURE__ */ React.createElement("label", { className: "term-label", htmlFor: "ai-profile" }, "AI profile"), /* @__PURE__ */ React.createElement(
    "select",
    {
      id: "ai-profile",
      className: "term-input",
      value: aiProfile,
      onChange: (e) => setAiProfile(e.target.value)
    },
    AI_PROFILES.map((p) => /* @__PURE__ */ React.createElement("option", { key: p, value: p }, p))
  ), /* @__PURE__ */ React.createElement(
    "button",
    {
      className: "term-btn",
      type: "button",
      disabled: aiM.isPending,
      onClick: () => aiM.mutate()
    },
    aiM.isPending ? "REQUESTING\u2026" : aiM.data ? "REFRESH AI OPINION" : "REQUEST AI OPINION"
  ), aiM.isError && /* @__PURE__ */ React.createElement("span", { className: "text-term-amber" }, "\u26A0 ", friendlyAIError(aiM.error), "\u2014 deterministic forecast above is unaffected.")), /* @__PURE__ */ React.createElement(
    AIOpinionCard,
    {
      opinion: aiM.data ?? null,
      deterministicProbability: f?.probability,
      deterministicDirection: dirWord,
      provenance: f?.provenance,
      onRequest: aiM.data ? void 0 : () => aiM.mutate(),
      requesting: aiM.isPending,
      requestError: aiM.isError ? friendlyAIError(aiM.error) : null
    }
  ));
}
function AnalyticsGrid({ title, data }) {
  const entries = Object.entries(data ?? {});
  const visible = entries.slice(0, MAX_ANALYTICS_CELLS);
  return /* @__PURE__ */ React.createElement("div", { className: "mt-2" }, /* @__PURE__ */ React.createElement("p", { className: "text-xs font-bold text-term-text" }, title), entries.length === 0 ? /* @__PURE__ */ React.createElement("p", { className: "text-xs text-term-muted" }, "unavailable") : /* @__PURE__ */ React.createElement(React.Fragment, null, /* @__PURE__ */ React.createElement("dl", { className: "mt-1 grid grid-cols-2 gap-1 text-xs md:grid-cols-4" }, visible.map(([k, v]) => /* @__PURE__ */ React.createElement("div", { key: k, className: "rounded border border-term-border px-2 py-1" }, /* @__PURE__ */ React.createElement("dt", { className: "text-term-muted" }, k), /* @__PURE__ */ React.createElement("dd", { className: "font-bold" }, typeof v === "object" ? JSON.stringify(v) : String(v))))), entries.length > visible.length && /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-[10px] text-term-muted", role: "status" }, "showing first ", visible.length, " of ", entries.length, " \u2014 +", entries.length - visible.length, " more.")));
}
export { ForecastDetails as default };
