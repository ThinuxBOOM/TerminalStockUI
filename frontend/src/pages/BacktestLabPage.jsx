import React, { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { FORECAST_HORIZONS, runBacktest } from "../api/client";
import { getBacktestHistory, getRecentBacktests, saveRecentBacktest } from "../api/backtestHistory";
import useWatchlist from "../hooks/useWatchlist";
import ProvenanceBadge from "../components/ProvenanceBadge";
import FreshnessBadge from "../components/FreshnessBadge";
import CalibrationChart from "../components/CalibrationChart";
import Loading from "../components/Loading";
import ErrorState, { StaleBanner } from "../components/ErrorState";
const MAX_HISTORY_ROWS = 20;
const MAX_RELIABILITY_ROWS = 20;
function BacktestLabPage() {
  const [searchParams] = useSearchParams();
  const [symbol, setSymbol] = useState(
    () => searchParams.get("symbol")?.trim().toUpperCase() || "AAPL"
  );
  const [horizons, setHorizons] = useState([21]);
  const [formError, setFormError] = useState(null);
  const { add: addToWatchlist } = useWatchlist();
  useEffect(() => {
    const s = searchParams.get("symbol")?.trim().toUpperCase();
    if (s) setSymbol(s);
  }, [searchParams]);
  const lab = useMutation({
    mutationFn: ({ s, h }) => runBacktest(s, h),
    onSuccess: (_data, vars) => {
      saveRecentBacktest(vars.s, vars.h);
    }
  });
  const trimmedSymbol = symbol.trim().toUpperCase();
  // History follows a debounced symbol: typing "AAPL" must not fire
  // four sequential history fetches.
  const [historySymbol, setHistorySymbol] = useState(trimmedSymbol);
  useEffect(() => {
    const t = setTimeout(() => setHistorySymbol(symbol.trim().toUpperCase()), 500);
    return () => clearTimeout(t);
  }, [symbol]);
  const historyQ = useQuery({
    queryKey: ["backtest-history", historySymbol],
    queryFn: () => getBacktestHistory(historySymbol, true),
    enabled: historySymbol.length > 0,
    staleTime: 3e4,
    retry: false
  });
  const recent = useMemo(
    () => getRecentBacktests(),
    [lab.submittedAt, lab.isSuccess]
  );
  const historyRows = useMemo(() => historyQ.data ?? [], [historyQ.data]);
  const visibleHistory = useMemo(
    () => historyRows.slice(0, MAX_HISTORY_ROWS),
    [historyRows]
  );
  function toggle(h) {
    setHorizons((prev) => prev.includes(h) ? prev.filter((x) => x !== h) : [...prev, h].sort());
  }
  function run() {
    const s = symbol.trim().toUpperCase();
    if (!s) {
      setFormError("Enter a symbol (e.g. AAPL, 600519.SS, MC.PA).");
      return;
    }
    if (horizons.length === 0) {
      setFormError("Select at least one horizon.");
      return;
    }
    setFormError(null);
    lab.mutate({ s, h: horizons });
  }
  function rerun(entrySymbol, entryHorizons) {
    const s = entrySymbol.trim().toUpperCase();
    if (!s) return;
    const h = entryHorizons.length > 0 ? [...entryHorizons].sort((a, b) => a - b) : horizons;
    if (h.length === 0) {
      setFormError("Select at least one horizon.");
      return;
    }
    setSymbol(s);
    setHorizons(h);
    setFormError(null);
    lab.mutate({ s, h });
  }
  const r = lab.data ?? void 0;
  return /* @__PURE__ */ React.createElement("div", null, /* @__PURE__ */ React.createElement("h1", { className: "mb-3 text-sm tracking-widest text-term-muted" }, "BACKTEST LAB \xB7 LIGHTWEIGHT (V1)"), /* @__PURE__ */ React.createElement("section", { className: "term-panel p-4" }, /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-end gap-3" }, /* @__PURE__ */ React.createElement("div", null, /* @__PURE__ */ React.createElement("label", { className: "term-label", htmlFor: "bt-symbol" }, "Symbol"), /* @__PURE__ */ React.createElement(
    "input",
    {
      id: "bt-symbol",
      className: "term-input mt-1 w-48",
      value: symbol,
      onChange: (e) => setSymbol(e.target.value),
      placeholder: "AAPL",
      spellCheck: false
    }
  )), /* @__PURE__ */ React.createElement("div", null, /* @__PURE__ */ React.createElement("p", { className: "term-label" }, "Horizons (trading days)"), /* @__PURE__ */ React.createElement("div", { className: "mt-1 flex gap-2" }, FORECAST_HORIZONS.map((h) => /* @__PURE__ */ React.createElement("label", { key: h, className: "flex cursor-pointer items-center gap-1 text-sm" }, /* @__PURE__ */ React.createElement(
    "input",
    {
      type: "checkbox",
      checked: horizons.includes(h),
      onChange: () => toggle(h)
    }
  ), h, "d")))), /* @__PURE__ */ React.createElement("button", { className: "term-btn", type: "button", disabled: lab.isPending, onClick: run }, lab.isPending ? "RUNNING\u2026" : "\u25B6 RUN BACKTEST")), formError && /* @__PURE__ */ React.createElement("p", { className: "mt-2 text-xs text-term-red", role: "alert" }, formError), /* @__PURE__ */ React.createElement("p", { className: "mt-2 text-[11px] text-term-muted" }, "Walk-forward only, time-ordered splits, corporate-action adjusted prices. Brier score (0 = perfect, 0.25 = coin-flip) and ECE (lower = better calibrated).")), /* @__PURE__ */ React.createElement("div", { className: "mt-4" }, lab.isPending && /* @__PURE__ */ React.createElement(Loading, { label: `backtesting ${symbol.trim().toUpperCase()}\u2026` }), lab.isError && /* @__PURE__ */ React.createElement(
    ErrorState,
    {
      title: "Backtest failed",
      detail: lab.error instanceof Error ? `${lab.error.message} \u2014 backend /api/backtest unreachable or rejected.` : "Backend /api/backtest unreachable or rejected.",
      onRetry: run
    }
  ), !lab.isPending && !lab.isError && !r && /* @__PURE__ */ React.createElement("div", { className: "term-panel p-6 text-sm text-term-muted" }, "Pick a symbol + horizon and run. Results show Brier score, ECE, and the reliability table \u2014 failures included."), r && /* @__PURE__ */ React.createElement(React.Fragment, null, /* @__PURE__ */ React.createElement(LabResults, { r }), /* @__PURE__ */ React.createElement("section", { className: "term-panel mt-4 flex flex-wrap items-center gap-2 p-4" }, /* @__PURE__ */ React.createElement(
    "button",
    {
      className: "term-btn",
      type: "button",
      onClick: () => addToWatchlist(r.symbol, "backtest")
    },
    "+ ADD ",
    r.symbol,
    " TO WATCHLIST"
  ), /* @__PURE__ */ React.createElement(
    Link,
    {
      className: "term-btn-ghost text-xs",
      to: `/forecast/${encodeURIComponent(r.symbol)}`
    },
    "VIEW FORECAST \u2192"
  ), /* @__PURE__ */ React.createElement("span", { className: "text-[11px] text-term-muted" }, "Saved to recent backtests; forecast shows this run in its calibration history."))), /* @__PURE__ */ React.createElement("section", { className: "term-panel mt-4 p-4" }, /* @__PURE__ */ React.createElement("p", { className: "term-label" }, "Recent backtests \xB7 this browser"), recent.length === 0 ? /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-xs text-term-muted" }, "No backtests run yet in this browser \u2014 runs persist here after each success.") : /* @__PURE__ */ React.createElement("ul", { className: "mt-2 space-y-1 text-xs" }, recent.map((entry) => /* @__PURE__ */ React.createElement(
    "li",
    {
      key: `${entry.symbol}-${entry.at}`,
      className: "flex flex-wrap items-center justify-between gap-2 border-b border-term-border pb-1"
    },
    /* @__PURE__ */ React.createElement("span", null, /* @__PURE__ */ React.createElement("b", { className: "text-term-green" }, entry.symbol), /* @__PURE__ */ React.createElement("span", { className: "ml-2 text-term-muted" }, entry.horizons.length > 0 ? entry.horizons.map((h) => `${h}d`).join(", ") : "\u2014"), /* @__PURE__ */ React.createElement("span", { className: "ml-2 text-[10px] text-term-muted" }, new Date(entry.at).toLocaleString())),
    /* @__PURE__ */ React.createElement("span", { className: "flex gap-2" }, /* @__PURE__ */ React.createElement(
      "button",
      {
        className: "term-btn-ghost text-xs",
        type: "button",
        onClick: () => rerun(entry.symbol, entry.horizons),
        disabled: lab.isPending
      },
      "RE-RUN"
    ), /* @__PURE__ */ React.createElement(
      Link,
      {
        className: "text-term-green",
        to: `/forecast/${encodeURIComponent(entry.symbol)}`
      },
      "FORECAST \u2192"
    ))
  )))), /* @__PURE__ */ React.createElement("section", { className: "term-panel mt-4 p-4" }, /* @__PURE__ */ React.createElement("p", { className: "term-label" }, "Persisted history \xB7 ", trimmedSymbol || "\u2014", " (backend)"), historyQ.isLoading && /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-xs text-term-muted" }, "loading run history\u2026"), historyQ.isError && /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-xs text-term-amber" }, "\u26A0 run history unavailable \u2014 backend /api/backtest/", trimmedSymbol || "\u2026", " unreachable."), !historyQ.isLoading && !historyQ.isError && historyRows.length === 0 && /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-xs text-term-muted" }, "No persisted runs for ", trimmedSymbol || "this symbol", " yet \u2014 run a backtest above."), historyRows.length > 0 && /* @__PURE__ */ React.createElement("div", { className: "mt-2 overflow-x-auto" }, historyRows.length > visibleHistory.length && /* @__PURE__ */ React.createElement("p", { className: "mb-1 text-[11px] text-term-muted", role: "status" }, "showing first ", visibleHistory.length, " of ", historyRows.length, " runs."), /* @__PURE__ */ React.createElement("table", { className: "w-full text-xs" }, /* @__PURE__ */ React.createElement("thead", null, /* @__PURE__ */ React.createElement("tr", { className: "text-left text-term-muted" }, /* @__PURE__ */ React.createElement("th", { className: "py-1 pr-2" }, "Run"), /* @__PURE__ */ React.createElement("th", { className: "py-1 pr-2" }, "As of"), /* @__PURE__ */ React.createElement("th", { className: "py-1 pr-2" }, "Horizons"), /* @__PURE__ */ React.createElement("th", { className: "py-1 pr-2" }, "Brier"), /* @__PURE__ */ React.createElement("th", { className: "py-1 pr-2" }, "ECE"), /* @__PURE__ */ React.createElement("th", { className: "py-1 pr-2" }, "n"))), /* @__PURE__ */ React.createElement("tbody", null, visibleHistory.map((run2, i) => {
    const keys = Object.keys(run2.metrics ?? {});
    const first = keys.length > 0 ? run2.metrics[keys[0]] : void 0;
    const horizons2 = run2.horizons ?? [];
    const scored = keys.length > 0 ? keys[0] : null;
    return /* @__PURE__ */ React.createElement("tr", { key: run2.run_id ?? `run-${i}`, className: "border-t border-term-border" }, /* @__PURE__ */ React.createElement("td", { className: "py-1 pr-2 font-mono text-[11px]" }, String(run2.run_id ?? "").slice(0, 8) || "\u2014"), /* @__PURE__ */ React.createElement("td", { className: "py-1 pr-2 text-term-muted" }, run2.as_of ?? "\u2014"), /* @__PURE__ */ React.createElement("td", { className: "py-1 pr-2" }, horizons2.length > 0 ? horizons2.map((h) => `${h}d`).join(", ") : "\u2014"), /* @__PURE__ */ React.createElement("td", { className: "py-1 pr-2" }, first?.brier === null || first?.brier === void 0 ? "\u2014" : Number(first.brier).toFixed(4), scored !== null && /* @__PURE__ */ React.createElement("span", { className: "ml-1 text-[10px] text-term-muted" }, "\xB7", scored, "d")), /* @__PURE__ */ React.createElement("td", { className: "py-1 pr-2" }, first?.ece === null || first?.ece === void 0 ? "\u2014" : Number(first.ece).toFixed(4)), /* @__PURE__ */ React.createElement("td", { className: "py-1 pr-2" }, first?.n_points ?? "\u2014"));
  })))))));
}
function LabResults({ r }) {
  const stale = r.provenance?.fallback_used === true || (r.provenance?.delay_minutes ?? 0) > 30;
  const scoredHorizon = (r.horizons ?? [])[0];
  const scoredSuffix = scoredHorizon !== void 0 ? ` \xB7 ${scoredHorizon}d` : "";
  const reliability = useMemo(() => r.reliability ?? [], [r]);
  const failures = useMemo(() => r.failures ?? [], [r]);
  const visibleBins = useMemo(
    () => reliability.slice(0, MAX_RELIABILITY_ROWS),
    [reliability]
  );
  const verdicts = useMemo(() => {
    const out = [];
    if (r.brier === null || r.brier === void 0) {
      out.push({ ok: false, text: "Brier score unavailable \u2014 too few resolved windows." });
    } else if (r.brier <= 0.25) {
      out.push({ ok: true, text: `Brier ${r.brier.toFixed(4)} beats the coin-flip baseline (0.25).` });
    } else {
      out.push({ ok: false, text: `Brier ${r.brier.toFixed(4)} is worse than coin-flip (0.25) \u2014 model adds no skill here.` });
    }
    if (r.ece === null || r.ece === void 0) {
      out.push({ ok: false, text: "ECE unavailable." });
    } else if (r.ece <= 0.05) {
      out.push({ ok: true, text: `ECE ${r.ece.toFixed(4)} \u2014 well calibrated.` });
    } else if (r.ece <= 0.1) {
      out.push({ ok: true, text: `ECE ${r.ece.toFixed(4)} \u2014 roughly calibrated.` });
    } else {
      out.push({ ok: false, text: `ECE ${r.ece.toFixed(4)} \u2014 poorly calibrated, treat probabilities with skepticism.` });
    }
    const emptyBins = reliability.filter(
      (b) => !(typeof b?.mean_predicted === "number" && Number.isFinite(b.mean_predicted))
    ).length;
    if (emptyBins > 0) {
      out.push({ ok: false, text: `${emptyBins} calibration bin(s) empty \u2014 thin history at those probability levels.` });
    }
    for (const f of failures) out.push({ ok: false, text: f });
    return out;
  }, [r, reliability, failures]);
  return /* @__PURE__ */ React.createElement("div", { className: "space-y-4" }, stale && /* @__PURE__ */ React.createElement(StaleBanner, { detail: `backtest via ${r.provenance?.source ?? "unknown"}, delay ${r.provenance?.delay_minutes ?? "\u2014"}m` }), /* @__PURE__ */ React.createElement("section", { className: "grid gap-4 md:grid-cols-3" }, /* @__PURE__ */ React.createElement("div", { className: "term-panel p-4" }, /* @__PURE__ */ React.createElement("p", { className: "term-label" }, "Brier score \xB7 ", r.symbol, scoredSuffix), /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-2xl font-bold" }, r.brier === null || r.brier === void 0 ? "\u2014" : r.brier.toFixed(4)), /* @__PURE__ */ React.createElement("div", { className: "mt-1 flex flex-wrap gap-1" }, /* @__PURE__ */ React.createElement(ProvenanceBadge, { p: r.provenance }), /* @__PURE__ */ React.createElement(FreshnessBadge, { p: r.provenance }))), /* @__PURE__ */ React.createElement("div", { className: "term-panel p-4" }, /* @__PURE__ */ React.createElement("p", { className: "term-label" }, "ECE (calibration error)", scoredSuffix), /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-2xl font-bold" }, r.ece === null || r.ece === void 0 ? "\u2014" : r.ece.toFixed(4)), /* @__PURE__ */ React.createElement("div", { className: "mt-1 flex flex-wrap gap-1" }, /* @__PURE__ */ React.createElement(ProvenanceBadge, { p: r.provenance }), /* @__PURE__ */ React.createElement(FreshnessBadge, { p: r.provenance }))), /* @__PURE__ */ React.createElement("div", { className: "term-panel p-4" }, /* @__PURE__ */ React.createElement("p", { className: "term-label" }, "Coverage"), /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-2xl font-bold" }, r.n_windows ?? "\u2014", /* @__PURE__ */ React.createElement("span", { className: "ml-1 text-xs font-normal text-term-muted" }, "windows")), /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-xs text-term-muted" }, "horizons: ", (r.horizons ?? []).length > 0 ? (r.horizons ?? []).map((h) => `${h}d`).join(", ") : "\u2014"), /* @__PURE__ */ React.createElement("div", { className: "mt-1" }, /* @__PURE__ */ React.createElement(ProvenanceBadge, { p: r.provenance })))), /* @__PURE__ */ React.createElement("section", { className: "term-panel p-4" }, /* @__PURE__ */ React.createElement(CalibrationChart, { rows: reliability, title: `Reliability diagram${scoredSuffix}` }), /* @__PURE__ */ React.createElement("div", { className: "mt-2 overflow-x-auto" }, reliability.length > visibleBins.length && /* @__PURE__ */ React.createElement("p", { className: "mb-1 text-[11px] text-term-muted", role: "status" }, "showing first ", visibleBins.length, " of ", reliability.length, " bins."), /* @__PURE__ */ React.createElement("table", { className: "w-full text-xs" }, /* @__PURE__ */ React.createElement("thead", null, /* @__PURE__ */ React.createElement("tr", { className: "text-left text-term-muted" }, /* @__PURE__ */ React.createElement("th", { className: "py-1 pr-2" }, "Bin"), /* @__PURE__ */ React.createElement("th", { className: "py-1 pr-2" }, "n"), /* @__PURE__ */ React.createElement("th", { className: "py-1 pr-2" }, "Mean predicted"), /* @__PURE__ */ React.createElement("th", { className: "py-1 pr-2" }, "Fraction positive"))), /* @__PURE__ */ React.createElement("tbody", null, reliability.length === 0 && /* @__PURE__ */ React.createElement("tr", { className: "border-t border-term-border" }, /* @__PURE__ */ React.createElement("td", { colSpan: 4, className: "py-2 text-term-muted" }, "No reliability rows returned.")), visibleBins.map((b, i) => /* @__PURE__ */ React.createElement("tr", { key: `${String(b?.bin_low ?? "?")}-${String(b?.bin_high ?? "?")}-${i}`, className: "border-t border-term-border" }, /* @__PURE__ */ React.createElement("td", { className: "py-1 pr-2" }, typeof b?.bin_low === "number" && Number.isFinite(b.bin_low) ? b.bin_low.toFixed(2) : "\u2014", "\u2013", typeof b?.bin_high === "number" && Number.isFinite(b.bin_high) ? b.bin_high.toFixed(2) : "\u2014"), /* @__PURE__ */ React.createElement("td", { className: "py-1 pr-2" }, b?.count ?? "\u2014"), /* @__PURE__ */ React.createElement("td", { className: "py-1 pr-2" }, typeof b?.mean_predicted === "number" && Number.isFinite(b.mean_predicted) ? b.mean_predicted.toFixed(3) : "\u2014"), /* @__PURE__ */ React.createElement("td", { className: "py-1 pr-2" }, typeof b?.fraction_positive === "number" && Number.isFinite(b.fraction_positive) ? b.fraction_positive.toFixed(3) : "\u2014")))))), /* @__PURE__ */ React.createElement("div", { className: "mt-2" }, /* @__PURE__ */ React.createElement(ProvenanceBadge, { p: r.provenance }))), /* @__PURE__ */ React.createElement("section", { className: "term-panel p-4" }, /* @__PURE__ */ React.createElement("p", { className: "term-label" }, "Verdicts \xB7 successes and failures"), /* @__PURE__ */ React.createElement("ul", { className: "mt-2 space-y-1 text-xs" }, verdicts.map((v, i) => /* @__PURE__ */ React.createElement("li", { key: i, className: v.ok ? "text-term-green" : "text-term-red" }, v.ok ? "\u2713 " : "\u2715 ", v.text))), r.notes && /* @__PURE__ */ React.createElement("p", { className: "mt-2 text-[11px] text-term-muted" }, r.notes), /* @__PURE__ */ React.createElement("p", { className: "mt-2 text-[11px] text-term-muted" }, "Not investment advice.")));
}
export { BacktestLabPage as default };
