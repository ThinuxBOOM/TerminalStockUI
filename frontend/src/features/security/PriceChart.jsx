import React, { useEffect, useMemo, useRef } from "react";
import { createChart, ColorType } from "lightweight-charts";
import ProvenanceBadge from "../../components/ProvenanceBadge";
import FreshnessBadge from "../../components/FreshnessBadge";
import Skeleton from "../../components/Skeleton";
function toUTCTime(d) {
  const ms = (/* @__PURE__ */ new Date(`${d}T12:00:00Z`)).getTime();
  if (!Number.isFinite(ms)) return null;
  return Math.floor(ms / 1e3);
}
function isValidCandle(c) {
  return !!c && typeof c.time === "string" && /^\d{4}-\d{2}-\d{2}/.test(c.time) && Number.isFinite(c.open) && Number.isFinite(c.high) && Number.isFinite(c.low) && Number.isFinite(c.close);
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
function PriceChart({
  symbol,
  data,
  loading = false,
  error = null,
  provenance = null
}) {
  const chartRef = useRef(null);
  const seriesRef = useRef(null);
  const roRef = useRef(null);
  const sanitized = useMemo(() => (data ?? []).filter(isValidCandle), [data]);
  const live = sanitized.length > 0 ? sanitized : null;
  const candles = live ?? [];
  // Lazily create the chart when its container attaches (works across the
  // loading -> live branch switch). Destroyed once on unmount — never
  // recreated per data change.
  const setContainerRef = (node) => {
    if (!node || chartRef.current) return;
    const chart = createChart(node, {
      layout: { background: { type: ColorType.Solid, color: "#0f141d" }, textColor: "#8b94a7" },
      grid: { vertLines: { color: "#1c2433" }, horzLines: { color: "#1c2433" } },
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
      chart.applyOptions({ width: node.clientWidth });
    });
    ro.observe(node);
    roRef.current = ro;
  };
  useEffect(() => () => {
    if (roRef.current) { roRef.current.disconnect(); roRef.current = null; }
    if (chartRef.current) { chartRef.current.remove(); chartRef.current = null; seriesRef.current = null; }
  }, []);
  // Data-only updates: no teardown, no refit churn on identical data.
  useEffect(() => {
    if (!seriesRef.current || !chartRef.current) return;
    const points = toPoints(candles);
    seriesRef.current.setData(points);
    if (points.length > 0) chartRef.current.timeScale().fitContent();
  }, [candles]);
  // Loading with no live bars yet: skeleton frame — never synthetic OHLC
  // that could be mistaken for market data.
  if (!live && loading && !error) {
    return /* @__PURE__ */ React.createElement(Skeleton, { label: `loading live bars for ${symbol}…`, lines: 8, className: "min-h-[300px]" });
  }
  if (!live && !loading) {
    return /* @__PURE__ */ React.createElement("div", { role: "status" }, /* @__PURE__ */ React.createElement("p", { className: "py-8 text-center text-xs text-term-muted" }, "Price history unavailable \u2014", " ", error ?? "the bars endpoint returned no candles for this symbol.", " No placeholder is shown in place of market data."), /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-[10px] text-term-muted" }, "source: GET /api/market_data/bars \xB7 symbol ", symbol), provenance && /* @__PURE__ */ React.createElement("div", { className: "mt-2 flex flex-wrap gap-2" }, /* @__PURE__ */ React.createElement(ProvenanceBadge, { p: provenance }), /* @__PURE__ */ React.createElement(FreshnessBadge, { p: provenance })));
  }
  return /* @__PURE__ */ React.createElement("div", null, /* @__PURE__ */ React.createElement(
    "div",
    {
      ref: setContainerRef,
      className: "w-full",
      role: "img",
      "aria-label": `price chart for ${symbol}, ${candles.length} bars`
    }
  ), /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-[10px] text-term-muted" }, provenance?.fallback_used ? `fallback bars from /api/market_data/bars \xB7 ${candles.length} bars (see badge \u2014 last close tracks the header quote, history is not market data)` : `live bars from /api/market_data/bars \xB7 ${candles.length} bars`), provenance && /* @__PURE__ */ React.createElement("div", { className: "mt-2 flex flex-wrap gap-2" }, /* @__PURE__ */ React.createElement(ProvenanceBadge, { p: provenance }), /* @__PURE__ */ React.createElement(FreshnessBadge, { p: provenance })));
}
export { PriceChart as default };
