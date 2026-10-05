import React from "react";
import { TipRow, useChartTooltip } from "./ChartTooltip";
import { pct } from "./Inline";

// Vertical bars around a zero baseline (e.g. forward return by decile).
// Single series: one hue; values labelled at the two extremes only.
function ColumnChart({ data = [], format = (v) => pct(v, 2), height = 200, xLabel, ariaLabel }) {
  const tip = useChartTooltip();
  if (!data.length) return <p className="text-sm text-term-muted">No data.</p>;
  const W = 560;
  const H = 200;
  const M = { top: 16, right: 12, bottom: 30, left: 52 };
  const vals = data.map((d) => d.value);
  const lo = Math.min(0, ...vals);
  const hi = Math.max(0, ...vals);
  const pad = (hi - lo) * 0.12 || 0.001;
  const y0 = lo - (lo < 0 ? pad : 0);
  const y1 = hi + pad;
  const y = (v) => M.top + ((y1 - v) / (y1 - y0)) * (H - M.top - M.bottom);
  const band = (W - M.left - M.right) / data.length;
  const bw = Math.min(24, band * 0.6);
  const maxI = vals.indexOf(Math.max(...vals));
  const minI = vals.indexOf(Math.min(...vals));
  const ticks = [y0 + (y1 - y0) * 0.1, (y0 + y1) / 2, y1 - (y1 - y0) * 0.1];
  return (
    <div ref={tip.ref} className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height }} role="img" aria-label={ariaLabel} onMouseLeave={tip.hide}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={M.left} x2={W - M.right} y1={y(t)} y2={y(t)} stroke="var(--chart-grid)" />
            <text x={M.left - 8} y={y(t) + 4} textAnchor="end" fontSize="11" fill="var(--term-muted)" className="tnum">{format(t)}</text>
          </g>
        ))}
        <line x1={M.left} x2={W - M.right} y1={y(0)} y2={y(0)} stroke="var(--chart-axis)" />
        {data.map((d, i) => {
          const cx = M.left + band * i + band / 2;
          const top = Math.min(y(d.value), y(0));
          const h = Math.max(1, Math.abs(y(d.value) - y(0)));
          const r = Math.min(4, h / 2);
          const up = d.value >= 0;
          // 4px rounded data-end, square at the baseline.
          const path = up
            ? `M${cx - bw / 2},${y(0)}V${top + r}Q${cx - bw / 2},${top} ${cx - bw / 2 + r},${top}H${cx + bw / 2 - r}Q${cx + bw / 2},${top} ${cx + bw / 2},${top + r}V${y(0)}Z`
            : `M${cx - bw / 2},${y(0)}V${top + h - r}Q${cx - bw / 2},${top + h} ${cx - bw / 2 + r},${top + h}H${cx + bw / 2 - r}Q${cx + bw / 2},${top + h} ${cx + bw / 2},${top + h - r}V${y(0)}Z`;
          return (
            <g key={d.label} onMouseMove={(e) => tip.show(e, <div><div className="mb-1 font-semibold text-term-text">{d.tip ?? d.label}</div><TipRow label="Value" value={format(d.value)} />{d.extra}</div>)}>
              <rect x={cx - band / 2} y={M.top} width={band} height={H - M.top - M.bottom} fill="transparent" />
              <path d={path} fill={d.color ?? "var(--series-1)"} />
              {(i === maxI || i === minI) ? (
                <text x={cx} y={up ? top - 5 : top + h + 13} textAnchor="middle" fontSize="11" fill="var(--term-text)" className="tnum">{format(d.value)}</text>
              ) : null}
              <text x={cx} y={H - 10} textAnchor="middle" fontSize="11" fill="var(--term-muted)">{d.label}</text>
            </g>
          );
        })}
      </svg>
      {xLabel ? <div className="text-center text-2xs text-term-faint">{xLabel}</div> : null}
      {tip.node}
    </div>
  );
}

// Horizontal bars with labels and values (risk contributions, weights).
function BarList({ data = [], format = (v) => `${(v * 100).toFixed(1)}%`, max }) {
  const top = max ?? Math.max(...data.map((d) => Math.abs(d.value)), 1e-9);
  return (
    <ul className="space-y-2">
      {data.map((d) => (
        <li key={d.label} className="grid grid-cols-[minmax(4rem,8rem)_1fr_4.5rem] items-center gap-3 text-sm">
          <span className="truncate text-term-text" title={d.label}>{d.label}</span>
          <span className="relative h-2 rounded-full bg-term-panel2">
            <span className="absolute inset-y-0 left-0 rounded-full" style={{ width: `${Math.max(2, (Math.abs(d.value) / top) * 100)}%`, background: d.color ?? "var(--series-1)" }} />
          </span>
          <span className="term-num text-right text-term-text">{format(d.value)}</span>
        </li>
      ))}
    </ul>
  );
}

// Correlation matrix: diverging blue (+) / red (-) through a neutral midpoint.
function Heatmap({ labels = [], matrix = [] }) {
  const tip = useChartTooltip();
  const n = labels.length;
  if (!n) return null;
  const color = (v) => {
    if (typeof v !== "number") return "var(--div-mid)";
    const a = Math.min(1, Math.abs(v));
    return `color-mix(in oklab, ${v >= 0 ? "var(--div-pos)" : "var(--div-neg)"} ${Math.round(a * 100)}%, var(--div-mid))`;
  };
  const cell = n > 10 ? 26 : 38;
  return (
    <div ref={tip.ref} className="relative overflow-x-auto">
      <table className="border-separate" style={{ borderSpacing: 2 }} aria-label="Correlation matrix">
        <thead>
          <tr>
            <th />
            {labels.map((l) => <th key={l} className="px-1 text-2xs font-medium text-term-muted" style={{ width: cell }}>{l}</th>)}
          </tr>
        </thead>
        <tbody>
          {matrix.map((row, i) => (
            <tr key={labels[i]}>
              <th className="pr-2 text-right text-2xs font-medium text-term-muted">{labels[i]}</th>
              {row.map((v, j) => (
                <td
                  key={labels[j]}
                  className="term-num rounded-sm text-center text-2xs"
                  style={{ background: color(v), width: cell, height: cell, color: Math.abs(v ?? 0) > 0.55 ? "#fff" : "var(--term-text)" }}
                  onMouseMove={(e) => tip.show(e, <div><div className="mb-1 font-semibold text-term-text">{labels[i]} vs {labels[j]}</div><TipRow label="Correlation" value={typeof v === "number" ? v.toFixed(2) : "—"} /></div>)}
                  onMouseLeave={tip.hide}
                >
                  {n <= 10 && typeof v === "number" ? (Math.abs(v) < 0.005 ? "0.00" : v.toFixed(2)) : ""}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="mt-2 flex items-center gap-2 text-2xs text-term-muted">
        <span>−1</span>
        <span className="h-2 w-32 rounded-full" style={{ background: "linear-gradient(90deg, var(--div-neg), var(--div-mid), var(--div-pos))" }} />
        <span>+1</span>
      </div>
      {tip.node}
    </div>
  );
}

// Calibration: predicted probability vs observed frequency, with the diagonal.
function Reliability({ rows = [], height = 220 }) {
  const tip = useChartTooltip();
  if (!rows.length) return null;
  const W = 300;
  const H = 220;
  const M = 30;
  const ps = rows.flatMap((r) => [r.mean_p, r.observed]);
  const lo = Math.max(0, Math.floor((Math.min(...ps) - 0.02) * 50) / 50);
  const hi = Math.min(1, Math.ceil((Math.max(...ps) + 0.02) * 50) / 50);
  const s = (v, a, b) => a + ((v - lo) / (hi - lo)) * (b - a);
  const x = (v) => s(v, M, W - 10);
  const y = (v) => s(v, H - M, 10);
  return (
    <div ref={tip.ref} className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height }} role="img" aria-label="Predicted vs observed">
        <line x1={x(lo)} y1={y(lo)} x2={x(hi)} y2={y(hi)} stroke="var(--chart-axis)" strokeDasharray="0" />
        {[lo, (lo + hi) / 2, hi].map((t) => (
          <g key={t}>
            <text x={x(t)} y={H - 10} textAnchor="middle" fontSize="10" fill="var(--term-muted)">{Math.round(t * 100)}%</text>
            <text x={M - 6} y={y(t) + 3} textAnchor="end" fontSize="10" fill="var(--term-muted)">{Math.round(t * 100)}%</text>
          </g>
        ))}
        {rows.map((r, i) => (
          <circle
            key={i}
            cx={x(r.mean_p)}
            cy={y(r.observed)}
            r="5"
            fill="var(--series-1)"
            stroke="var(--term-panel)"
            strokeWidth="2"
            onMouseMove={(e) => tip.show(e, <div><TipRow label="Predicted" value={pct(r.mean_p, 1).replace("+", "")} /><TipRow label="Observed" value={pct(r.observed, 1).replace("+", "")} /><TipRow label="Points" value={r.n?.toLocaleString?.() ?? r.n} /></div>)}
            onMouseLeave={tip.hide}
          />
        ))}
      </svg>
      <div className="text-center text-2xs text-term-faint">Predicted (x) vs. observed (y); on the line = well calibrated</div>
      {tip.node}
    </div>
  );
}

export { BarList, ColumnChart, Heatmap, Reliability };
