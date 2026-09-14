import React, { useMemo } from "react";
const GREEN = "#3ddc84";
const RED = "#ff5c5c";
const MUTED = "#5b6b85";
const GRID = "#1c2433";
const AXIS = "#2a3448";
const compactFmt = new Intl.NumberFormat("en", {
  notation: "compact",
  maximumFractionDigits: 1
});
function compact(v) {
  if (v === null || v === void 0 || !Number.isFinite(v)) return "\u2014";
  try {
    return compactFmt.format(v);
  } catch {
    return String(v);
  }
}
function finite(v) {
  return typeof v === "number" && Number.isFinite(v);
}
function CrossMarketChart({ markets }) {
  const rows = useMemo(() => markets ?? [], [markets]);
  const W = 360;
  const rowH = 26;
  const padL = 64;
  const padR = 56;
  const padT = 8;
  const H = padT + rows.length * rowH + 18;
  const maxAbs = useMemo(() => {
    let m = 0.5;
    for (const r of rows) {
      const v = Math.abs(r?.avg_change_pct ?? 0);
      if (Number.isFinite(v) && v > m) m = v;
    }
    return m;
  }, [rows]);
  const maxVol = useMemo(() => {
    let m = 1;
    for (const r of rows) {
      const v = r?.total_volume ?? 0;
      if (typeof v === "number" && Number.isFinite(v) && v > m) m = v;
    }
    return m;
  }, [rows]);
  const cx = padL + (W - padL - padR) / 2;
  const half = (W - padL - padR) / 2;
  const x = (v) => cx + Math.max(-maxAbs, Math.min(maxAbs, v)) / maxAbs * half;
  return /* @__PURE__ */ React.createElement("div", { className: "grid min-w-0 gap-3 lg:grid-cols-2" }, /* @__PURE__ */ React.createElement("figure", { className: "min-w-0" }, /* @__PURE__ */ React.createElement("figcaption", { className: "term-label mb-1" }, "Avg change % by market"), /* @__PURE__ */ React.createElement(
    "svg",
    {
      viewBox: `0 0 ${W} ${H}`,
      className: "w-full rounded border border-term-border bg-term-bg",
      role: "img",
      "aria-label": `average change percent per market: ${rows.map((m) => `${m.mic} ${m.avg_change_pct?.toFixed(2) ?? "unavailable"}%`).join(", ")}`
    },
    /* @__PURE__ */ React.createElement("line", { x1: cx, y1: padT, x2: cx, y2: H - 18, stroke: AXIS }),
    rows.map((m, i) => {
      const y = padT + i * rowH;
      const v = m.avg_change_pct;
      const has = finite(v);
      const bx = has ? Math.min(cx, x(v)) : cx;
      const bw = has ? Math.max(2, Math.abs(x(v) - cx)) : 0;
      const col = !has ? MUTED : v > 0 ? GREEN : v < 0 ? RED : MUTED;
      return /* @__PURE__ */ React.createElement("g", { key: `${m.mic}-${i}` }, /* @__PURE__ */ React.createElement("text", { x: padL - 6, y: y + 14, fill: MUTED, fontSize: 10, textAnchor: "end" }, m.mic), has ? /* @__PURE__ */ React.createElement("rect", { x: bx, y: y + 4, width: bw, height: 12, rx: 2, fill: col, fillOpacity: 0.8 }, /* @__PURE__ */ React.createElement("title", null, `${m.label}: ${(v > 0 ? "+" : "") + v.toFixed(2)}%`)) : null, /* @__PURE__ */ React.createElement("text", { x: W - padR + 6, y: y + 14, fill: MUTED, fontSize: 10 }, has ? `${(v > 0 ? "+" : "") + v.toFixed(2)}%` : "\u2014"));
    }),
    /* @__PURE__ */ React.createElement("text", { x: cx, y: H - 5, fill: MUTED, fontSize: 9, textAnchor: "middle" }, "0 \xB7 \xB1", maxAbs.toFixed(1), "%")
  )), /* @__PURE__ */ React.createElement("figure", { className: "min-w-0" }, /* @__PURE__ */ React.createElement("figcaption", { className: "term-label mb-1" }, "Total volume by market"), /* @__PURE__ */ React.createElement(
    "svg",
    {
      viewBox: `0 0 ${W} ${H}`,
      className: "w-full rounded border border-term-border bg-term-bg",
      role: "img",
      "aria-label": `total volume per market: ${rows.map((m) => `${m.mic} ${m.total_volume ?? "unavailable"}`).join(", ")}`
    },
    rows.map((m, i) => {
      const y = padT + i * rowH;
      const v = m.total_volume;
      const w = finite(v) && v > 0 ? Math.max(2, v / maxVol * (W - padL - padR)) : 0;
      return /* @__PURE__ */ React.createElement("g", { key: `${m.mic}-${i}` }, /* @__PURE__ */ React.createElement("text", { x: padL - 6, y: y + 14, fill: MUTED, fontSize: 10, textAnchor: "end" }, m.mic), w > 0 ? /* @__PURE__ */ React.createElement("rect", { x: padL, y: y + 4, width: w, height: 12, rx: 2, fill: "#3b82a0", fillOpacity: 0.8 }, /* @__PURE__ */ React.createElement("title", null, `${m.label}: ${compact(v)} shares`)) : null, /* @__PURE__ */ React.createElement("text", { x: W - padR + 6, y: y + 14, fill: MUTED, fontSize: 10 }, compact(v)));
    }),
    /* @__PURE__ */ React.createElement("text", { x: padL, y: H - 5, fill: MUTED, fontSize: 9 }, "max ", compact(maxVol), " shares")
  )));
}
function MarketDetailGraphs({ rows }) {
  const all = useMemo(() => (rows ?? []).filter((r) => r && typeof r.symbol === "string" && r.symbol !== ""), [rows]);
  const MAX_DETAIL_ROWS = 30;
  const capped = useMemo(() => all.slice(0, MAX_DETAIL_ROWS), [all]);
  const cappedNote = all.length > capped.length ? `showing first ${capped.length} of ${all.length}` : null;
  const W = 360;
  const rowH = 24;
  const padL = 92;
  const padR = 58;
  const padT = 8;
  const changes = useMemo(() => capped.filter((r) => finite(r.change_pct)), [capped]);
  const vols = useMemo(() => capped.filter((r) => finite(r.volume) && r.volume > 0), [capped]);
  const sorted = useMemo(
    () => [...capped].sort((a, b) => (b.change_pct ?? -Infinity) - (a.change_pct ?? -Infinity)),
    [capped]
  );
  const Hc = padT + Math.max(1, sorted.length) * rowH + 18;
  const Hv = padT + Math.max(1, capped.length) * rowH + 18;
  const maxAbs = useMemo(() => {
    let m = 0.5;
    for (const r of changes) {
      const v = Math.abs(r.change_pct);
      if (Number.isFinite(v) && v > m) m = v;
    }
    return m;
  }, [changes]);
  const maxVol = useMemo(() => {
    let m = 1;
    for (const r of vols) {
      const v = r.volume;
      if (Number.isFinite(v) && v > m) m = v;
    }
    return m;
  }, [vols]);
  const cx = padL + (W - padL - padR) / 2;
  const half = (W - padL - padR) / 2;
  const x = (v) => cx + Math.max(-maxAbs, Math.min(maxAbs, v)) / maxAbs * half;
  return /* @__PURE__ */ React.createElement("div", { className: "grid min-w-0 gap-3 lg:grid-cols-2" }, /* @__PURE__ */ React.createElement("figure", { className: "min-w-0" }, /* @__PURE__ */ React.createElement("figcaption", { className: "term-label mb-1" }, "Change % by symbol", cappedNote ? ` \xB7 ${cappedNote}` : ""), changes.length === 0 ? /* @__PURE__ */ React.createElement("p", { className: "rounded border border-term-border p-3 text-xs text-term-muted", role: "status" }, "No change data for these symbols yet.") : /* @__PURE__ */ React.createElement(
    "svg",
    {
      viewBox: `0 0 ${W} ${Hc}`,
      className: "w-full rounded border border-term-border bg-term-bg",
      role: "img",
      "aria-label": `change percent by symbol: ${sorted.map((r) => `${r.symbol} ${r.change_pct?.toFixed(2) ?? "unavailable"}%`).join(", ")}`
    },
    /* @__PURE__ */ React.createElement("line", { x1: cx, y1: padT, x2: cx, y2: Hc - 18, stroke: AXIS }),
    sorted.map((r, i) => {
      const y = padT + i * rowH;
      const v = r.change_pct;
      const has = finite(v);
      const bx = has ? Math.min(cx, x(v)) : cx;
      const bw = has ? Math.max(2, Math.abs(x(v) - cx)) : 0;
      const col = !has ? MUTED : v > 0 ? GREEN : v < 0 ? RED : MUTED;
      const label = r.symbol.length > 12 ? r.symbol.slice(0, 12) + "\u2026" : r.symbol;
      return /* @__PURE__ */ React.createElement("g", { key: `${r.symbol}-${i}` }, /* @__PURE__ */ React.createElement("text", { x: padL - 6, y: y + 13, fill: MUTED, fontSize: 9, textAnchor: "end" }, label), has ? /* @__PURE__ */ React.createElement("rect", { x: bx, y: y + 3, width: bw, height: 11, rx: 2, fill: col, fillOpacity: 0.8 }, /* @__PURE__ */ React.createElement("title", null, `${r.symbol}: ${(v > 0 ? "+" : "") + v.toFixed(2)}%`)) : null, /* @__PURE__ */ React.createElement("text", { x: W - padR + 6, y: y + 13, fill: MUTED, fontSize: 9 }, has ? `${(v > 0 ? "+" : "") + v.toFixed(2)}%` : "\u2014"));
    }),
    /* @__PURE__ */ React.createElement("text", { x: cx, y: Hc - 5, fill: MUTED, fontSize: 9, textAnchor: "middle" }, "0 \xB7 \xB1", maxAbs.toFixed(1), "%")
  )), /* @__PURE__ */ React.createElement("figure", { className: "min-w-0" }, /* @__PURE__ */ React.createElement("figcaption", { className: "term-label mb-1" }, "Volume by symbol", cappedNote ? ` \xB7 ${cappedNote}` : ""), vols.length === 0 ? /* @__PURE__ */ React.createElement("p", { className: "rounded border border-term-border p-3 text-xs text-term-muted", role: "status" }, "No volume data for these symbols yet \u2014 screener fallback carries no volume.") : /* @__PURE__ */ React.createElement(
    "svg",
    {
      viewBox: `0 0 ${W} ${Hv}`,
      className: "w-full rounded border border-term-border bg-term-bg",
      role: "img",
      "aria-label": `volume by symbol: ${capped.map((r) => `${r.symbol} ${r.volume ?? "unavailable"}`).join(", ")}`
    },
    [0.5, 1].map((t) => /* @__PURE__ */ React.createElement(
      "line",
      {
        key: t,
        x1: padL + (W - padL - padR) * t,
        y1: padT,
        x2: padL + (W - padL - padR) * t,
        y2: Hv - 18,
        stroke: GRID,
        strokeWidth: 1
      }
    )),
    capped.map((r, i) => {
      const y = padT + i * rowH;
      const v = r.volume;
      const w = finite(v) && v > 0 ? Math.max(2, v / maxVol * (W - padL - padR)) : 0;
      const label = r.symbol.length > 12 ? r.symbol.slice(0, 12) + "\u2026" : r.symbol;
      return /* @__PURE__ */ React.createElement("g", { key: `${r.symbol}-${i}` }, /* @__PURE__ */ React.createElement("text", { x: padL - 6, y: y + 13, fill: MUTED, fontSize: 9, textAnchor: "end" }, label), w > 0 ? /* @__PURE__ */ React.createElement("rect", { x: padL, y: y + 3, width: w, height: 11, rx: 2, fill: "#3b82a0", fillOpacity: 0.8 }, /* @__PURE__ */ React.createElement("title", null, `${r.symbol}: ${compact(v)} shares`)) : null, /* @__PURE__ */ React.createElement("text", { x: W - padR + 6, y: y + 13, fill: MUTED, fontSize: 9 }, compact(v)));
    }),
    /* @__PURE__ */ React.createElement("text", { x: padL, y: Hv - 5, fill: MUTED, fontSize: 9 }, "max ", compact(maxVol))
  )));
}
export { CrossMarketChart, MarketDetailGraphs };
