import React, { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createChart, CrosshairMode, LineStyle } from "lightweight-charts";
import ProvenanceBadge from "../../components/ProvenanceBadge";
import FreshnessBadge from "../../components/FreshnessBadge";
import CurrencyValue from "../../components/CurrencyValue";
import Skeleton from "../../components/Skeleton";
import ErrorState from "../../components/ErrorState";

const h = React.createElement;

// ---------------------------------------------------------------------------
// PriceChart - candlestick chart (lightweight-charts) with indicator overlays.
//
// Props:
//   symbol               string - display symbol, e.g. "AAPL" (labels only)
//   data                 Array<{time:"YYYY-MM-DD",open,high,low,close}> | null -
//                          raw daily candles from GET /api/market_data/bars.
//                          NEVER synthetic: invalid rows are dropped, loading
//                          renders a Skeleton, empty renders an honest note.
//   loading              bool - bars request in flight
//   error                string|null - bars failure message (no fake bars)
//   provenance           object|null - bars provenance envelope (badges + note)
//   indicators           object - NORMALIZED indicator map from
//                          normalizeIndicators() in src/api/client.js:
//                          { SMA20:[{time,value}], BB_UPPER:[...],
//                            MACD_LINE/MACD_SIGNAL/MACD_HIST, RSI14, ... }.
//                          Missing keys render nothing (never fabricated).
//   requestedIndicators  Array<string> - canonical names the page asked for
//                          (SMA20/SMA50/SMA200/EMA12/EMA26/RSI14/MACD/BB20/
//                          VWAP/ATR14). Drives the legend + empty-state copy.
//   indicatorsLoading    bool - indicator request in flight (per-pane skeleton)
//   indicatorsError      string|null - indicator failure (ErrorState, chart ok)
//   indicatorsProvenance object|null - indicator provenance (badge only)
//   currency             string - ISO code for legend CurrencyValue formatting
//   onRetryIndicators    func|null - retry callback for indicator ErrorState
//
// Panes:
//   price pane - candles + SMA/EMA/BB/VWAP line overlays.
//   oscillator pane (separate chart, only when an oscillator was requested) -
//     RSI14 / ATR14 lines + MACD line/signal + MACD histogram. RSI 70/30
//     guides are scale constants via createPriceLine (not market data).
// Backend contract (Backend Agent 5):
//   GET /api/analytics/{symbol}?indicators=SMA20,EMA12,RSI14,MACD,BB20,VWAP,ATR14
// ---------------------------------------------------------------------------

// Max raw bars accepted; display is decimated to MAX_DISPLAY_CANDLES so the
// chart stays well under 60fps (500 candles + a handful of <=1000pt lines).
const MAX_BARS_SUPPORTED = 1000;
const MAX_DISPLAY_CANDLES = 500;

const DASHED = LineStyle && LineStyle.Dashed ? LineStyle.Dashed : 2;
const SOLID = LineStyle && LineStyle.Solid ? LineStyle.Solid : 0;

// Overlay line colors (term palette: grid #1c2433, text #8b94a7,
// up #3ddc84, down #ff5c5c, cyan #56c8ff for composite/analogue lines).
// Keys match normalizeIndicators.
const INDICATOR_COLORS = {
  SMA20: "#f5c542",
  SMA50: "#56c8ff",
  SMA200: "#b388ff",
  EMA12: "#ff9f43",
  EMA26: "#00d1b2",
  BB_UPPER: "#8b94a7",
  BB_MIDDLE: "#c3cad6",
  BB_LOWER: "#8b94a7",
  VWAP: "#56c8ff",
  RSI14: "#b388ff",
  ATR14: "#56c8ff",
  MACD_LINE: "#56c8ff",
  MACD_SIGNAL: "#ff9f43"
};

// Overlays drawn on the price pane vs the separate oscillator pane.
const PRICE_OVERLAY_KEYS = ["SMA20", "SMA50", "SMA200", "EMA12", "EMA26", "BB_UPPER", "BB_MIDDLE", "BB_LOWER", "VWAP"];
const OSC_LINE_KEYS = ["RSI14", "ATR14", "MACD_LINE", "MACD_SIGNAL"];
const HIST_KEY = "MACD_HIST";
const HIST_UP = "#3ddc84";
const HIST_DOWN = "#ff5c5c";

// Canonical request name -> legend/series expansion.
function expandRequested(requested) {
  const out = [];
  for (const name of requested || []) {
    if (name === "BB20") {
      out.push({ key: "BB_UPPER", label: "BB20 upper", pane: "price" });
      out.push({ key: "BB_MIDDLE", label: "BB20 middle", pane: "price" });
      out.push({ key: "BB_LOWER", label: "BB20 lower", pane: "price" });
    } else if (name === "MACD") {
      out.push({ key: "MACD_LINE", label: "MACD line", pane: "osc" });
      out.push({ key: "MACD_SIGNAL", label: "MACD signal", pane: "osc" });
      out.push({ key: HIST_KEY, label: "MACD hist", pane: "osc" });
    } else if (PRICE_OVERLAY_KEYS.indexOf(name) !== -1) {
      out.push({ key: name, label: name, pane: "price" });
    } else if (OSC_LINE_KEYS.indexOf(name) !== -1) {
      out.push({ key: name, label: name, pane: "osc" });
    }
  }
  return out;
}

function toUTCTime(d) {
  const ms = (/* @__PURE__ */ new Date(`${d}T12:00:00Z`)).getTime();
  if (!Number.isFinite(ms)) return null;
  return Math.floor(ms / 1e3);
}

function isValidCandle(c) {
  return !!c && typeof c.time === "string" && /^\d{4}-\d{2}-\d{2}/.test(c.time) && Number.isFinite(c.open) && Number.isFinite(c.high) && Number.isFinite(c.low) && Number.isFinite(c.close);
}

// Bucket-merge decimation: groups raw candles into <= max buckets, each bucket
// keeping open(first), high(max), low(min), close(last) - extremes preserved,
// last close always tracks the latest bar. O(n), no synthetic prices.
function decimateCandles(candles, max) {
  const cap = max || MAX_DISPLAY_CANDLES;
  const sorted = [...(Array.isArray(candles) ? candles : [])].sort((a, b) =>
    String(a?.time ?? "") < String(b?.time ?? "") ? -1 : String(a?.time ?? "") > String(b?.time ?? "") ? 1 : 0
  );
  if (sorted.length <= cap) return sorted;
  const bucketSize = sorted.length / cap;
  const out = [];
  for (let b = 0; b < cap; b += 1) {
    const start = Math.floor(b * bucketSize);
    const end = b === cap - 1 ? sorted.length : Math.floor((b + 1) * bucketSize);
    if (end <= start) continue;
    const slice = sorted.slice(start, end);
    let high = -Infinity;
    let low = Infinity;
    for (const c of slice) {
      if (c.high > high) high = c.high;
      if (c.low < low) low = c.low;
    }
    out.push({ time: slice[0].time, open: slice[0].open, high, low, close: slice[slice.length - 1].close });
  }
  return out;
}

function toPoints(candles) {
  const points = [];
  for (const c of candles) {
    const t = toUTCTime(c.time);
    if (t === null) continue;
    points.push({ time: t, open: c.open, high: c.high, low: c.low, close: c.close });
  }
  return points;
}

// Normalized [{time:"YYYY-MM-DD", value}] -> lightweight-charts [{time:utc,value}].
// Null/NaN values are dropped (line gaps), times sorted + deduped (last wins).
function toLinePoints(series) {
  if (!Array.isArray(series)) return [];
  const pts = [];
  for (const p of series) {
    if (!p || typeof p !== "object") continue;
    const t = toUTCTime(p.time);
    const v = Number(p.value);
    if (t === null || !Number.isFinite(v)) continue;
    pts.push({ time: t, value: v });
  }
  pts.sort((a, b) => a.time - b.time);
  const out = [];
  for (const p of pts) {
    const prev = out[out.length - 1];
    if (prev && prev.time === p.time) prev.value = p.value;
    else out.push(p);
  }
  return out;
}

function toHistPoints(series) {
  return toLinePoints(series).map((p) => ({ time: p.time, value: p.value, color: p.value >= 0 ? HIST_UP : HIST_DOWN }));
}

const oscFmt = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });

function lastValueOf(indicators, key) {
  const arr = indicators ? indicators[key] : null;
  if (!Array.isArray(arr) || arr.length === 0) return null;
  const v = Number(arr[arr.length - 1].value);
  return Number.isFinite(v) ? v : null;
}

function PriceChart({
  symbol,
  data,
  loading = false,
  error = null,
  provenance = null,
  indicators = null,
  requestedIndicators = [],
  indicatorsLoading = false,
  indicatorsError = null,
  indicatorsProvenance = null,
  currency = "USD",
  onRetryIndicators = null
}) {
  const chartRef = useRef(null);
  const seriesRef = useRef(null);
  const overlayRef = useRef(new Map());
  const oscChartRef = useRef(null);
  const oscLinesRef = useRef(new Map());
  const oscHistRef = useRef(null);
  const rsiGuidesRef = useRef(false);
  const roRef = useRef(null);
  const oscRoRef = useRef(null);
  const resizeT = useRef(null);
  const oscResizeT = useRef(null);
  const lastFitRef = useRef(null);

  const sanitized = useMemo(() => (data || []).filter(isValidCandle), [data]);
  const candles = useMemo(() => decimateCandles(sanitized, MAX_DISPLAY_CANDLES), [sanitized]);
  const decimatedCount = sanitized.length - candles.length;
  const live = candles.length > 0 ? candles : null;

  const legendEntries = useMemo(() => expandRequested(requestedIndicators), [requestedIndicators]);
  const [hidden, setHidden] = useState(() => new Set());
  useEffect(() => {
    setHidden(new Set());
  }, [symbol]);
  const toggle = useCallback((key) => {
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const visiblePriceKeys = useMemo(
    () => legendEntries.filter((e) => e.pane === "price" && !hidden.has(e.key) && Array.isArray(indicators && indicators[e.key]) && indicators[e.key].length > 0).map((e) => e.key),
    [legendEntries, hidden, indicators]
  );
  const visibleOscKeys = useMemo(
    () => legendEntries.filter((e) => e.pane === "osc" && e.key !== HIST_KEY && !hidden.has(e.key) && Array.isArray(indicators && indicators[e.key]) && indicators[e.key].length > 0).map((e) => e.key),
    [legendEntries, hidden, indicators]
  );
  const histVisible = !hidden.has(HIST_KEY) && Array.isArray(indicators && indicators[HIST_KEY]) && indicators[HIST_KEY].length > 0;
  const hasPriceOverlayData = visiblePriceKeys.length > 0;
  const hasOscData = visibleOscKeys.length > 0 || histVisible;
  const oscRequested = useMemo(
    () => (requestedIndicators || []).some((n) => n === "RSI14" || n === "MACD" || n === "ATR14"),
    [requestedIndicators]
  );
  // Oscillator pane shows while its data loads/fails, or when requested and the
  // backend returned snapshot-only payloads (then it shows an honest note).
  const showOscPane = oscRequested || indicatorsLoading || indicatorsError || hasOscData;
  const priceOverlaysRequested = useMemo(
    () => (requestedIndicators || []).some((n) => n !== "RSI14" && n !== "MACD" && n !== "ATR14"),
    [requestedIndicators]
  );

  // Lazily create the price chart when its container attaches (works across the
  // loading -> live branch switch). Destroyed once on unmount - never
  // recreated per data change.
  const setContainerRef = useCallback((node) => {
    if (!node || chartRef.current) return;
    const chart = createChart(node, {
      layout: { background: { type: "solid", color: "#0f141d" }, textColor: "#8b94a7" },
      grid: { vertLines: { color: "#1c2433" }, horzLines: { color: "#1c2433" } },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { visible: true, labelVisible: true, color: "#8b94a7", style: 2 },
        horzLine: { visible: true, labelVisible: true, color: "#8b94a7", style: 2 },
      },
      height: 300
    });
    const series = chart.addCandlestickSeries({
      upColor: "#3ddc84",
      downColor: "#ff5c5c",
      wickUpColor: "#3ddc84",
      wickDownColor: "#ff5c5c",
      borderVisible: false
    });
    chartRef.current = chart;
    seriesRef.current = series;
    const ro = new ResizeObserver(() => {
      if (resizeT.current) clearTimeout(resizeT.current);
      resizeT.current = setTimeout(() => {
        if (chartRef.current) chartRef.current.applyOptions({ width: node.clientWidth });
      }, 120);
    });
    ro.observe(node);
    roRef.current = ro;
  }, []);

  const setOscContainerRef = useCallback((node) => {
    if (!node || oscChartRef.current) return;
    const chart = createChart(node, {
      layout: { background: { type: "solid", color: "#0f141d" }, textColor: "#8b94a7" },
      grid: { vertLines: { color: "#1c2433" }, horzLines: { color: "#1c2433" } },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { visible: true, labelVisible: true, color: "#8b94a7", style: 2 },
        horzLine: { visible: true, labelVisible: true, color: "#8b94a7", style: 2 },
      },
      height: 140
    });
    oscChartRef.current = chart;
    const ro = new ResizeObserver(() => {
      if (oscResizeT.current) clearTimeout(oscResizeT.current);
      oscResizeT.current = setTimeout(() => {
        if (oscChartRef.current) oscChartRef.current.applyOptions({ width: node.clientWidth });
      }, 120);
    });
    ro.observe(node);
    oscRoRef.current = ro;
  }, []);

  useEffect(() => () => {
    if (resizeT.current) {
      clearTimeout(resizeT.current);
      resizeT.current = null;
    }
    if (oscResizeT.current) {
      clearTimeout(oscResizeT.current);
      oscResizeT.current = null;
    }
    if (roRef.current) {
      roRef.current.disconnect();
      roRef.current = null;
    }
    if (oscRoRef.current) {
      oscRoRef.current.disconnect();
      oscRoRef.current = null;
    }
    if (chartRef.current) {
      chartRef.current.remove();
      chartRef.current = null;
      seriesRef.current = null;
      overlayRef.current = new Map();
    }
    if (oscChartRef.current) {
      oscChartRef.current.remove();
      oscChartRef.current = null;
      oscLinesRef.current = new Map();
      oscHistRef.current = null;
      rsiGuidesRef.current = false;
    }
  }, []);

  // Data-only updates: no teardown. Candles + price-pane overlays.
  useEffect(() => {
    if (!seriesRef.current || !chartRef.current) return;
    const points = toPoints(candles);
    seriesRef.current.setData(points);
    const alive = overlayRef.current;
    for (const [key, s] of Array.from(alive.entries())) {
      if (visiblePriceKeys.indexOf(key) === -1) {
        try {
          chartRef.current.removeSeries(s);
        } catch {
          // already removed - ignore
        }
        alive.delete(key);
      }
    }
    for (const key of visiblePriceKeys) {
      const pts = toLinePoints(indicators[key]);
      if (pts.length === 0) continue;
      let s = alive.get(key);
      if (!s) {
        s = chartRef.current.addLineSeries({
          color: INDICATOR_COLORS[key] || "#ffffff",
          lineWidth: key === "BB_UPPER" || key === "BB_LOWER" ? 1 : 2,
          lineStyle: key === "BB_UPPER" || key === "BB_LOWER" ? DASHED : SOLID,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false
        });
        alive.set(key, s);
      }
      s.setData(pts);
    }
    // Refit only when the candle set itself changes (toggles keep zoom).
    if (points.length > 0 && lastFitRef.current !== candles) {
      lastFitRef.current = candles;
      chartRef.current.timeScale().fitContent();
    }
  }, [candles, indicators, visiblePriceKeys]);

  // Oscillator pane updates: RSI/ATR/MACD lines + MACD histogram.
  useEffect(() => {
    if (!oscChartRef.current || !showOscPane) return;
    const chart = oscChartRef.current;
    const alive = oscLinesRef.current;
    for (const [key, s] of Array.from(alive.entries())) {
      if (visibleOscKeys.indexOf(key) === -1) {
        try {
          chart.removeSeries(s);
        } catch {
          // already removed - ignore
        }
        alive.delete(key);
      }
    }
    for (const key of visibleOscKeys) {
      const pts = toLinePoints(indicators[key]);
      if (pts.length === 0) continue;
      let s = alive.get(key);
      if (!s) {
        s = chart.addLineSeries({
          color: INDICATOR_COLORS[key] || "#ffffff",
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false
        });
        alive.set(key, s);
        if (key === "RSI14" && !rsiGuidesRef.current) {
          // Scale guides (constants, not market data): overbought/oversold.
          try {
            s.createPriceLine({ price: 70, color: "#ff5c5c", lineWidth: 1, lineStyle: DASHED, axisLabelVisible: true, title: "70" });
            s.createPriceLine({ price: 30, color: "#3ddc84", lineWidth: 1, lineStyle: DASHED, axisLabelVisible: true, title: "30" });
            rsiGuidesRef.current = true;
          } catch {
            // older builds without createPriceLine - guides skipped, lines intact
          }
        }
      }
      s.setData(pts);
    }
    if (histVisible) {
      const pts = toHistPoints(indicators[HIST_KEY]);
      if (pts.length > 0) {
        if (!oscHistRef.current) {
          oscHistRef.current = chart.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false });
        }
        oscHistRef.current.setData(pts);
      }
    } else if (oscHistRef.current) {
      try {
        chart.removeSeries(oscHistRef.current);
      } catch {
        // already removed - ignore
      }
      oscHistRef.current = null;
    }
    if (hasOscData) chart.timeScale().fitContent();
  }, [indicators, visibleOscKeys, histVisible, hasOscData, showOscPane]);

  // Loading with no live bars yet: skeleton frame - never synthetic OHLC
  // that could be mistaken for market data. Separate empty (no error) from
  // error so a null-error empty never renders the word "unavailable" as if
  // it were a failure, and vice versa.
  if (!live && loading && !error) {
    return /* @__PURE__ */ h(Skeleton, { label: `loading live bars for ${symbol}…`, lines: 8, className: "min-h-[300px]" });
  }
  if (!live && !loading && error) {
    return /* @__PURE__ */ h("div", { role: "alert" }, /* @__PURE__ */ h("p", { className: "py-8 text-center text-xs text-term-muted" }, "Price history unavailable —", " ", error, " No placeholder is shown in place of market data."), /* @__PURE__ */ h("p", { className: "mt-1 text-[10px] text-term-muted" }, "source: GET /api/market_data/bars · symbol ", symbol), provenance && /* @__PURE__ */ h("div", { className: "mt-2 flex flex-wrap gap-2" }, /* @__PURE__ */ h(ProvenanceBadge, { p: provenance }), /* @__PURE__ */ h(FreshnessBadge, { p: provenance })));
  }
  if (!live && !loading && !error) {
    return /* @__PURE__ */ h("div", { role: "status" }, /* @__PURE__ */ h("p", { className: "py-8 text-center text-xs text-term-muted" }, "No price history returned — the bars endpoint returned no candles for this symbol (empty, not an error)."), /* @__PURE__ */ h("p", { className: "mt-1 text-[10px] text-term-muted" }, "source: GET /api/market_data/bars · symbol ", symbol), provenance && /* @__PURE__ */ h("div", { className: "mt-2 flex flex-wrap gap-2" }, /* @__PURE__ */ h(ProvenanceBadge, { p: provenance }), /* @__PURE__ */ h(FreshnessBadge, { p: provenance })));
  }

  const legend = legendEntries.length > 0 ? /* @__PURE__ */ h(
    "div",
    { className: "absolute left-2 top-2 z-10 flex max-w-[calc(100%-1rem)] flex-wrap items-center gap-1.5 rounded border border-term-border bg-term-panel/85 p-1.5", role: "group", "aria-label": `indicator overlays for ${symbol}` },
    legendEntries.map((e) => {
      const off = hidden.has(e.key);
      const v = lastValueOf(indicators, e.key);
      const isPrice = e.pane === "price";
      return /* @__PURE__ */ h(
        "button",
        {
          key: e.key,
          type: "button",
          onClick: () => toggle(e.key),
          "aria-pressed": String(!off),
          title: `${off ? "show" : "hide"} ${e.label}`,
          className: off ? "rounded border border-term-border px-2 py-0.5 text-[10px] text-term-muted opacity-40" : "rounded border border-term-border px-2 py-0.5 text-[10px] text-term-text"
        },
        /* @__PURE__ */ h("span", { "aria-hidden": "true", className: "mr-1 inline-block h-2 w-2 rounded-full", style: { background: INDICATOR_COLORS[e.key] || "#ffffff" } }),
        e.label,
        v !== null ? /* @__PURE__ */ h("span", { className: "ml-1 text-term-muted" }, isPrice ? /* @__PURE__ */ h(CurrencyValue, { value: v, currency }) : /* @__PURE__ */ h("b", { className: "text-term-text" }, oscFmt.format(v))) : /* @__PURE__ */ h("span", { className: "ml-1 italic text-term-muted" }, indicatorsLoading ? "…" : "n/a")
      );
    })
  ) : null;

  const indicatorStatus = /* @__PURE__ */ h(
    React.Fragment,
    null,
    indicatorsError && !hasPriceOverlayData && !hasOscData && priceOverlaysRequested ? /* @__PURE__ */ h("div", { className: "mb-2" }, /* @__PURE__ */ h(ErrorState, { title: "Indicators unavailable", detail: `${indicatorsError} - price candles unaffected, no lines fabricated.`, onRetry: onRetryIndicators || void 0 })) : null,
    !indicatorsLoading && !indicatorsError && priceOverlaysRequested && !hasPriceOverlayData && legendEntries.some((e) => e.pane === "price") ? /* @__PURE__ */ h("p", { className: "mb-2 text-[11px] text-term-muted", role: "status" }, "Indicator series unavailable - the analytics endpoint returned snapshot values only (no plottable points). No lines fabricated.") : null
  );

  const oscPane = showOscPane ? /* @__PURE__ */ h(
    "div",
    { className: "mt-3" },
    /* @__PURE__ */ h("p", { className: "term-label" }, "Momentum · RSI / MACD / ATR"),
    indicatorsLoading && !hasOscData && !indicatorsError ? /* @__PURE__ */ h(Skeleton, { label: `loading oscillators for ${symbol}…`, lines: 2, className: "min-h-[140px]" }) : null,
    indicatorsError && !hasOscData ? /* @__PURE__ */ h(ErrorState, { title: "Oscillators unavailable", detail: `${indicatorsError} - price pane unaffected.`, onRetry: onRetryIndicators || void 0 }) : null,
    !indicatorsLoading && !indicatorsError && oscRequested && !hasOscData ? /* @__PURE__ */ h("p", { className: "py-4 text-center text-[11px] text-term-muted", role: "status" }, "Oscillator series unavailable - snapshot only, no lines fabricated.") : null,
    !indicatorsError && hasOscData ? /* @__PURE__ */ h("div", { ref: setOscContainerRef, className: "w-full min-h-[140px]", role: "img", "aria-label": `oscillators for ${symbol}` }) : null,
    indicatorsProvenance && hasOscData ? /* @__PURE__ */ h("div", { className: "mt-1" }, /* @__PURE__ */ h(ProvenanceBadge, { p: indicatorsProvenance })) : null
  ) : null;

  return /* @__PURE__ */ h(
    "div",
    null,
    loading && live ? /* @__PURE__ */ h("p", { className: "mb-1 text-[11px] text-term-muted", role: "status" }, "refreshing…") : null,
    indicatorsLoading && !hasPriceOverlayData && priceOverlaysRequested && live ? /* @__PURE__ */ h(Skeleton, { label: `loading overlays for ${symbol}…`, lines: 1 }) : null,
    indicatorStatus,
    /* @__PURE__ */ h("div", { className: "relative" },
      legend,
      /* @__PURE__ */ h("div", { ref: setContainerRef, className: "w-full min-h-[300px]", role: "img", "aria-label": `price chart for ${symbol}, ${candles.length} bars` })),
    oscPane,
    /* @__PURE__ */ h("p", { className: "mt-1 text-[10px] text-term-muted" }, `live bars from /api/market_data/bars · ${sanitized.length} received, ${candles.length} shown${decimatedCount > 0 ? ` (bucket-merged to ${MAX_DISPLAY_CANDLES}, extremes preserved)` : ""}`),
    provenance && /* @__PURE__ */ h("div", { className: "mt-2 flex flex-wrap gap-2" }, /* @__PURE__ */ h(ProvenanceBadge, { p: provenance }), /* @__PURE__ */ h(FreshnessBadge, { p: provenance }), indicatorsProvenance && (hasPriceOverlayData || hasOscData) ? /* @__PURE__ */ h(ProvenanceBadge, { p: indicatorsProvenance }) : null)
  );
}

const MemoPriceChart = memo(PriceChart);
export { MemoPriceChart as default, PriceChart, MAX_BARS_SUPPORTED, MAX_DISPLAY_CANDLES, INDICATOR_COLORS, decimateCandles };
