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
// ---------------------------------------------------------------------------
// Per-market liquidity primitives (Frontend Agent 2 revamp).
// All values stay native (no FX); missing data renders as placeholder,
// never faked.
// ---------------------------------------------------------------------------
function BreadthBar({ advancers = 0, decliners = 0, unchanged = 0, total = 0, mic = "" }) {
  const adv = Number.isFinite(Number(advancers)) && Number(advancers) > 0 ? Math.floor(Number(advancers)) : 0;
  const dec = Number.isFinite(Number(decliners)) && Number(decliners) > 0 ? Math.floor(Number(decliners)) : 0;
  const unch = Number.isFinite(Number(unchanged)) && Number(unchanged) > 0 ? Math.floor(Number(unchanged)) : 0;
  let t = Number(total);
  if (!Number.isFinite(t) || t <= 0) t = adv + dec + unch;
  else t = Math.floor(t);
  const advW = t > 0 ? (adv / t) * 100 : 0;
  const decW = t > 0 ? (dec / t) * 100 : 0;
  const unchW = t > 0 ? Math.max(0, 100 - advW - decW) : 0;
  const label = mic ? `${mic}: ${adv} advancers, ${dec} decliners, ${unch} unchanged` : `${adv} adv / ${dec} dec / ${unch} unch`;
  return React.createElement(
    "div",
    { className: "min-w-0" },
    React.createElement(
      "div",
      {
        className: "flex h-2 w-full overflow-hidden rounded bg-term-border",
        role: "img",
        "aria-label": label,
        title: `adv ${adv} / dec ${dec} / unch ${unch}`
      },
      React.createElement("div", { className: "h-full bg-term-green", style: { width: `${advW}%` } }),
      React.createElement("div", { className: "h-full bg-term-red", style: { width: `${decW}%` } }),
      React.createElement("div", { className: "h-full bg-term-muted", style: { width: `${unchW}%` } })
    ),
    React.createElement(
      "div",
      { className: "mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px]" },
      React.createElement("span", { className: "text-term-green" }, "▲ ", adv),
      React.createElement("span", { className: "text-term-red" }, "▼ ", dec),
      React.createElement("span", { className: "text-term-muted" }, "■ ", unch)
    )
  );
}
function NativeMeter({ value, max, currency = "USD", label = "Turnover", note }) {
  const has = finite(value);
  const pct = has && finite(max) && max > 0 && value > 0 ? Math.min(100, (value / max) * 100) : 0;
  const text = has ? compact(value) : "unavailable";
  return React.createElement(
    "div",
    { className: "min-w-0" },
    React.createElement(
      "div",
      { className: "flex items-center justify-between gap-2 text-xs" },
      React.createElement("span", { className: "text-term-muted" }, label),
      React.createElement(
        "span",
        { className: "text-term-text", title: note ?? `${label} in native ${currency} — no FX conversion` },
        text,
        has ? React.createElement("span", { className: "ml-1 text-[10px] text-term-muted" }, currency) : null
      )
    ),
    React.createElement(
      "div",
      {
        className: "mt-1 h-1.5 w-full overflow-hidden rounded bg-term-border",
        role: "img",
        "aria-label": `${label}: ${has ? `${text} ${currency}` : "unavailable"}`,
        title: has ? `${text} ${currency} (native, no FX)` : `${label} unavailable`
      },
      has && value > 0
        ? React.createElement("div", { className: "h-full rounded bg-[#3b82a0]", style: { width: `${Math.max(2, pct)}%` } })
        : null
    )
  );
}
function RangeBar({ value, max, label = "Avg range" }) {
  const has = finite(value);
  const pct = has && finite(max) && max > 0 && value > 0 ? Math.min(100, (value / max) * 100) : 0;
  const text = has ? `${value.toFixed(2)}%` : "unavailable";
  return React.createElement(
    "div",
    { className: "min-w-0" },
    React.createElement(
      "div",
      { className: "flex items-center justify-between gap-2 text-xs" },
      React.createElement("span", { className: "text-term-muted" }, label),
      React.createElement("span", { className: "text-term-text" }, text)
    ),
    React.createElement(
      "div",
      {
        className: "mt-1 h-1.5 w-full overflow-hidden rounded bg-term-border",
        role: "img",
        "aria-label": `${label}: ${text}`
      },
      has && value > 0
        ? React.createElement("div", { className: "h-full rounded bg-term-cyan", style: { width: `${Math.max(2, pct)}%` } })
        : null
    )
  );
}
function LiquiditySparkline({ history, market, mic = "" }) {
  const pts = Array.isArray(history?.points) ? history.points : [];
  const turnoverSeries = pts.filter((p) => finite(p?.turnover)).map((p) => p.turnover);
  const volumeSeries = pts.filter((p) => finite(p?.volume)).map((p) => p.volume);
  const breadthSeries = pts
    .filter((p) => Number.isFinite(Number(p?.advancers)) && Number.isFinite(Number(p?.decliners)))
    .map((p) => Number(p.advancers) - Number(p.decliners));
  let series = [];
  let kind = "turnover";
  if (turnoverSeries.length >= 2) {
    series = turnoverSeries;
    kind = "turnover";
  } else if (volumeSeries.length >= 2) {
    series = volumeSeries;
    kind = "volume";
  } else if (breadthSeries.length >= 2) {
    series = breadthSeries;
    kind = "breadth";
  } else {
    return React.createElement(
      "p",
      { className: "rounded border border-term-border p-2 text-[11px] text-term-muted", role: "status" },
      "No history yet — sparkline unavailable (backend /liquidity/history not deployed)."
    );
  }
  const W = 120;
  const H = 28;
  const min = Math.min(...series);
  const max = Math.max(...series);
  const span = max - min || 1;
  const step = series.length > 1 ? (W - 4) / (series.length - 1) : 0;
  const dots = series.map((v, i) => {
    const x = 2 + i * step;
    const y = H - 3 - ((v - min) / span) * (H - 6);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const labelMic = mic || market?.mic || history?.mic || "";
  return React.createElement(
    "figure",
    { className: "min-w-0" },
    React.createElement("figcaption", { className: "text-[10px] text-term-muted" }, `Trend (${kind})`),
    React.createElement(
      "svg",
      {
        viewBox: `0 0 ${W} ${H}`,
        className: "w-full rounded border border-term-border bg-term-bg",
        role: "img",
        "aria-label": `${labelMic} ${kind} trend: ${series.map((v) => compact(v)).join(", ")}`
      },
      React.createElement("polyline", {
        points: dots.join(" "),
        fill: "none",
        stroke: kind === "breadth" ? GREEN : "#3b82a0",
        strokeWidth: 1.5,
        strokeLinejoin: "round",
        strokeLinecap: "round"
      })
    )
  );
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
export { BreadthBar, CrossMarketChart, LiquiditySparkline, MarketDetailGraphs, NativeMeter, RangeBar };
