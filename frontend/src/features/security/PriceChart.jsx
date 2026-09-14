import React, { useEffect, useMemo, useRef } from "react";
import { createChart, ColorType } from "lightweight-charts";
import ProvenanceBadge from "../../components/ProvenanceBadge";
import FreshnessBadge from "../../components/FreshnessBadge";
function toUTCTime(d) {
  const ms = (/* @__PURE__ */ new Date(`${d}T12:00:00Z`)).getTime();
  if (!Number.isFinite(ms)) return null;
  return Math.floor(ms / 1e3);
}
function isValidCandle(c) {
  return !!c && typeof c.time === "string" && /^\d{4}-\d{2}-\d{2}/.test(c.time) && Number.isFinite(c.open) && Number.isFinite(c.high) && Number.isFinite(c.low) && Number.isFinite(c.close);
}
const EMPTY_CANDLES = [];
function seedCandles(symbol, n = 60) {
  let h = 0;
  for (const c of symbol) h = h * 31 + c.charCodeAt(0) >>> 0;
  let px = 100 + h % 80;
  const out = [];
  const today = Date.now();
  for (let i = n - 1; i >= 0; i--) {
    const drift = Math.sin((h + i) / 7) * 1.5;
    const open = px;
    const close = Math.max(1, open + drift);
    out.push({
      time: new Date(today - i * 864e5).toISOString().slice(0, 10),
      open,
      high: Math.max(open, close) * 1.01,
      low: Math.min(open, close) * 0.99,
      close
    });
    px = close;
  }
  return out;
}
function PriceChart({
  symbol,
  data,
  loading = false,
  error = null,
  provenance = null
}) {
  const ref = useRef(null);
  const placeholder = useMemo(() => seedCandles(symbol), [symbol]);
  const sanitized = useMemo(() => (data ?? []).filter(isValidCandle), [data]);
  const live = sanitized.length > 0 ? sanitized : null;
  const showPlaceholder = !live && loading && !error;
  const candles = useMemo(
    () => live ?? (showPlaceholder ? placeholder : EMPTY_CANDLES),
    [live, showPlaceholder, placeholder]
  );
  useEffect(() => {
    if (!ref.current || candles.length === 0) return;
    const chart = createChart(ref.current, {
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
    const points = [];
    for (const c of candles) {
      const t = toUTCTime(c.time);
      if (t === null) continue;
      points.push({ time: t, open: c.open, high: c.high, low: c.low, close: c.close });
    }
    if (points.length === 0) {
      chart.remove();
      return;
    }
    series.setData(points);
    chart.timeScale().fitContent();
    const ro = new ResizeObserver(() => {
      if (ref.current) chart.applyOptions({ width: ref.current.clientWidth });
    });
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.remove();
    };
  }, [symbol, candles]);
  if (!live && !showPlaceholder) {
    return /* @__PURE__ */ React.createElement("div", { role: "status" }, /* @__PURE__ */ React.createElement("p", { className: "py-8 text-center text-xs text-term-muted" }, "Price history unavailable \u2014", " ", error ?? "the bars endpoint returned no candles for this symbol.", " No placeholder is shown in place of market data."), /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-[10px] text-term-muted" }, "source: GET /api/market_data/bars \xB7 symbol ", symbol), provenance && /* @__PURE__ */ React.createElement("div", { className: "mt-2 flex flex-wrap gap-2" }, /* @__PURE__ */ React.createElement(ProvenanceBadge, { p: provenance }), /* @__PURE__ */ React.createElement(FreshnessBadge, { p: provenance })));
  }
  return /* @__PURE__ */ React.createElement("div", null, /* @__PURE__ */ React.createElement(
    "div",
    {
      ref,
      className: "w-full",
      role: "img",
      "aria-label": `price chart for ${symbol}, ${candles.length} bars`
    }
  ), showPlaceholder ? /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-[10px] text-term-amber", role: "status" }, "loading live bars from /api/market_data/bars \u2014 placeholder wave, not market data \xB7 ", candles.length, " bars") : /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-[10px] text-term-muted" }, provenance?.fallback_used ? `fallback bars from /api/market_data/bars \xB7 ${candles.length} bars (see badge \u2014 last close tracks the header quote, history is not market data)` : `live bars from /api/market_data/bars \xB7 ${candles.length} bars`), provenance && !showPlaceholder && /* @__PURE__ */ React.createElement("div", { className: "mt-2 flex flex-wrap gap-2" }, /* @__PURE__ */ React.createElement(ProvenanceBadge, { p: provenance }), /* @__PURE__ */ React.createElement(FreshnessBadge, { p: provenance })));
}
export { PriceChart as default, seedCandles };
