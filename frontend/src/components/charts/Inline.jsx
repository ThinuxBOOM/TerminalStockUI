import React from "react";

// Small inline visuals for tables and cards.

function pct(v, d = 1) {
  if (typeof v !== "number" || !Number.isFinite(v)) return "—";
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v * 100).toFixed(d)}%`;
}

// Return band on a symmetric scale: q10..q90 bar, median tick, zero line.
// `scale` is the half-width in return units shared by a table column so rows
// compare (defaults to the band's own extent).
function RangeBar({ low, mid, high, scale, height = 8, showLabels = false, className = "" }) {
  if (![low, high].every((v) => typeof v === "number" && Number.isFinite(v))) {
    return <span className="text-term-faint">—</span>;
  }
  const s = scale || Math.max(Math.abs(low), Math.abs(high), 0.01);
  const x = (v) => 50 + (Math.max(-s, Math.min(s, v)) / s) * 50;
  return (
    <div className={`flex items-center gap-2 ${className}`.trim()} aria-label={`range ${pct(low)} to ${pct(high)}`}>
      {showLabels ? <span className="term-num w-12 text-right text-2xs text-term-muted">{pct(low)}</span> : null}
      <svg viewBox="0 0 100 10" preserveAspectRatio="none" className="w-full min-w-[4rem]" style={{ height }}>
        <rect x="0" y="4.5" width="100" height="1" fill="var(--chart-grid)" />
        <rect x={x(low)} y="1" width={Math.max(0.8, x(high) - x(low))} height="8" rx="2" fill="var(--seq-2)" />
        <rect x="49.75" y="0" width="0.5" height="10" fill="var(--term-muted)" />
        {typeof mid === "number" ? <rect x={x(mid) - 0.6} y="0" width="1.2" height="10" fill="var(--term-text)" /> : null}
      </svg>
      {showLabels ? <span className="term-num w-12 text-2xs text-term-muted">{pct(high)}</span> : null}
    </div>
  );
}

// Percentile meter (0..1) with a marker; ticks at 10/50/90.
function RankMeter({ value, className = "", height = 6 }) {
  if (typeof value !== "number" || !Number.isFinite(value)) return <span className="text-term-faint">—</span>;
  const v = Math.max(0, Math.min(1, value));
  return (
    <div className={`relative w-full ${className}`.trim()} style={{ height }} aria-label={`percentile ${Math.round(v * 100)}`}>
      <div className="absolute inset-0 rounded-full bg-term-accentDim" />
      <div className="absolute inset-y-0 left-0 rounded-full bg-term-accent/70" style={{ width: `${v * 100}%` }} />
      {[0.1, 0.5, 0.9].map((t) => (
        <div key={t} className="absolute inset-y-0 w-px bg-term-bg/70" style={{ left: `${t * 100}%` }} />
      ))}
    </div>
  );
}

// Sparkline: line + 10% area wash in the series hue; last point dotted.
function Sparkline({ values = [], width = 96, height = 28, color = "var(--series-1)", className = "" }) {
  const pts = values.filter((v) => typeof v === "number" && Number.isFinite(v));
  if (pts.length < 2) return <span className="text-term-faint">—</span>;
  const min = Math.min(...pts);
  const max = Math.max(...pts);
  const span = max - min || 1;
  const step = width / (pts.length - 1);
  const xy = pts.map((v, i) => [i * step, height - 3 - ((v - min) / span) * (height - 6)]);
  const line = xy.map(([px, py], i) => `${i ? "L" : "M"}${px.toFixed(1)},${py.toFixed(1)}`).join("");
  const [lx, ly] = xy[xy.length - 1];
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} className={className} aria-hidden="true">
      <path d={`${line}L${width},${height}L0,${height}Z`} fill={color} opacity="0.1" />
      <path d={line} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={lx} cy={ly} r="2.5" fill={color} stroke="var(--term-panel)" strokeWidth="1.5" />
    </svg>
  );
}

export { RangeBar, RankMeter, Sparkline, pct };
