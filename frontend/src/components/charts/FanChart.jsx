import React, { useMemo } from "react";
import { TipRow, useChartTooltip } from "./ChartTooltip";
import { pct } from "./Inline";

const W = 640;
const H = 260;
const M = { top: 14, right: 64, bottom: 28, left: 52 };

function niceStep(span) {
  const raw = span / 4;
  const pow = 10 ** Math.floor(Math.log10(raw));
  const n = raw / pow;
  return (n < 1.5 ? 1 : n < 3 ? 2 : n < 7 ? 5 : 10) * pow;
}

function money(v, currency) {
  if (typeof v !== "number" || !Number.isFinite(v)) return "—";
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency: currency || "USD", currencyDisplay: "narrowSymbol", maximumFractionDigits: v >= 100 ? 0 : 2 }).format(v);
  } catch {
    return v.toFixed(2);
  }
}

// Return fan across horizons: 90% (5-95), 80% (10-90) and 50% (25-75)
// bands from today's close, plus the median path. One series, one hue in
// three sequential steps; the price axis on the right reads the same scale.
function FanChart({ horizons, lastClose, currency, height = 260 }) {
  const tip = useChartTooltip();
  const pts = useMemo(() => {
    const rows = Object.entries(horizons || {})
      .map(([h, f]) => ({ h: Number(h), q: f?.quantiles || {} }))
      .filter((r) => Number.isFinite(r.h) && r.q["0.10"] != null)
      .sort((a, b) => a.h - b.h);
    const zero = { h: 0, q: { "0.05": 0, "0.10": 0, "0.25": 0, "0.50": 0, "0.75": 0, "0.90": 0, "0.95": 0 } };
    return rows.length ? [zero, ...rows] : [];
  }, [horizons]);
  if (pts.length < 2) return <p className="text-sm text-term-muted">Forecast range unavailable.</p>;

  const maxH = pts[pts.length - 1].h;
  const lo = Math.min(...pts.map((p) => p.q["0.05"] ?? p.q["0.10"]));
  const hi = Math.max(...pts.map((p) => p.q["0.95"] ?? p.q["0.90"]));
  const step = niceStep(hi - lo || 0.02);
  const y0 = Math.floor(lo / step) * step;
  const y1 = Math.ceil(hi / step) * step;
  const x = (h) => M.left + (h / maxH) * (W - M.left - M.right);
  const y = (v) => M.top + ((y1 - v) / (y1 - y0)) * (H - M.top - M.bottom);
  const band = (a, b) => {
    const top = pts.map((p) => `${x(p.h)},${y(p.q[b] ?? 0)}`);
    const bottom = [...pts].reverse().map((p) => `${x(p.h)},${y(p.q[a] ?? 0)}`);
    return `M${top.join("L")}L${bottom.join("L")}Z`;
  };
  const median = `M${pts.map((p) => `${x(p.h)},${y(p.q["0.50"] ?? 0)}`).join("L")}`;
  const ticks = [];
  for (let v = y0; v <= y1 + 1e-9; v += step) ticks.push(Number(v.toFixed(6)));

  function onMove(e) {
    const svg = e.currentTarget.getBoundingClientRect();
    const hx = ((e.clientX - svg.left) / svg.width) * W;
    const nearest = pts.slice(1).reduce((a, b) => (Math.abs(x(b.h) - hx) < Math.abs(x(a.h) - hx) ? b : a));
    const q = nearest.q;
    const price = (r) => (lastClose ? ` · ${money(lastClose * (1 + r), currency)}` : "");
    tip.show(e, (
      <div>
        <div className="mb-1 font-semibold text-term-text">{nearest.h} trading day{nearest.h > 1 ? "s" : ""}</div>
        <TipRow label="95th pct" value={`${pct(q["0.95"])}${price(q["0.95"])}`} />
        <TipRow label="90th pct" value={`${pct(q["0.90"])}${price(q["0.90"])}`} swatch="var(--seq-2)" />
        <TipRow label="Median" value={`${pct(q["0.50"])}${price(q["0.50"])}`} swatch="var(--seq-4)" />
        <TipRow label="10th pct" value={`${pct(q["0.10"])}${price(q["0.10"])}`} swatch="var(--seq-2)" />
        <TipRow label="5th pct" value={`${pct(q["0.05"])}${price(q["0.05"])}`} />
      </div>
    ));
  }

  return (
    <div ref={tip.ref} className="relative">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        style={{ height }}
        role="img"
        aria-label="Forecast return range by horizon"
        onMouseMove={onMove}
        onMouseLeave={tip.hide}
      >
        {ticks.map((t) => (
          <g key={t}>
            <line x1={M.left} x2={W - M.right} y1={y(t)} y2={y(t)} stroke={t === 0 ? "var(--chart-axis)" : "var(--chart-grid)"} strokeWidth="1" />
            <text x={M.left - 8} y={y(t) + 4} textAnchor="end" fontSize="11" fill="var(--term-muted)" className="tnum">{pct(t, 0)}</text>
            {lastClose ? (
              <text x={W - M.right + 8} y={y(t) + 4} fontSize="11" fill="var(--term-faint)" className="tnum">{money(lastClose * (1 + t), currency)}</text>
            ) : null}
          </g>
        ))}
        <path d={band("0.05", "0.95")} fill="var(--series-1)" opacity="0.14" />
        <path d={band("0.10", "0.90")} fill="var(--series-1)" opacity="0.22" />
        <path d={band("0.25", "0.75")} fill="var(--series-1)" opacity="0.38" />
        <path d={median} fill="none" stroke="var(--seq-4)" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
        {pts.slice(1).map((p) => (
          <g key={p.h}>
            <line x1={x(p.h)} x2={x(p.h)} y1={M.top} y2={H - M.bottom} stroke="var(--chart-grid)" strokeWidth="1" />
            <circle cx={x(p.h)} cy={y(p.q["0.50"] ?? 0)} r="4" fill="var(--seq-4)" stroke="var(--term-panel)" strokeWidth="2" />
            <text x={x(p.h)} y={H - 8} textAnchor="middle" fontSize="11" fill="var(--term-muted)">{p.h}d</text>
          </g>
        ))}
        <text x={x(0) + 6} y={H - 8} textAnchor="end" fontSize="11" fill="var(--term-muted)">Today</text>
      </svg>
      {tip.node}
      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-2xs text-term-muted">
        <span className="flex items-center gap-1.5"><span className="inline-block h-2.5 w-4 rounded-sm" style={{ background: "var(--series-1)", opacity: 0.38 }} />50% of outcomes</span>
        <span className="flex items-center gap-1.5"><span className="inline-block h-2.5 w-4 rounded-sm" style={{ background: "var(--series-1)", opacity: 0.22 }} />80%</span>
        <span className="flex items-center gap-1.5"><span className="inline-block h-2.5 w-4 rounded-sm" style={{ background: "var(--series-1)", opacity: 0.14 }} />90%</span>
        <span className="flex items-center gap-1.5"><span className="inline-block h-0.5 w-4" style={{ background: "var(--seq-4)" }} />Median</span>
      </div>
    </div>
  );
}

export { FanChart, FanChart as default, money };
