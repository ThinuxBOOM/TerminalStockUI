import React, { useMemo } from "react";
import { sma } from "./demoData.js";

/* SVG-only price chart (no lightweight-charts). Reason for SVG: tiny,
 * dependency-free, transform-friendly. Shows the selected timeframe series
 * plus optional SMA20 overlay and volume bars. */
function pathFor(values, w, h, pad = 6) {
  const clean = values.filter((v) => v != null);
  if (!clean.length) return "";
  const min = Math.min(...clean);
  const max = Math.max(...clean);
  const span = max - min || 1;
  const stepX = (w - pad * 2) / Math.max(1, values.length - 1);
  return values
    .map((v, i) => {
      if (v == null) return "";
      const x = pad + i * stepX;
      const y = pad + (1 - (v - min) / span) * (h - pad * 2);
      return `${i === 0 || values[i - 1] == null ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}

function DemoChart({ data, timeframe, indicators }) {
  const W = 560;
  const H = 190;
  const series = data.series[timeframe] || data.series["1M"];
  const showSMA = indicators.includes("SMA20");
  const showVol = indicators.includes("VOL");
  const sma20 = useMemo(() => sma(series, 5), [series]);
  const up = data.changePct >= 0;
  const color = up ? "#3ddc84" : "#ff5c5c";

  const vols = data.volume.slice(-series.length);
  const maxVol = Math.max(...vols, 1);

  return (
    <figure style={{ margin: 0 }} aria-label={`Demo price chart for ${data.symbol}, timeframe ${timeframe}`}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height="auto" role="img" aria-hidden="false">
        {[0.25, 0.5, 0.75].map((f) => (
          <line key={f} x1="0" x2={W} y1={H * f} y2={H * f} stroke="#1c2433" strokeWidth="1" />
        ))}
        <path d={pathFor(series, W, showVol ? H - 44 : H)} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" />
        {showSMA ? (
          <path d={pathFor(sma20, W, showVol ? H - 44 : H)} fill="none" stroke="#56c8ff" strokeWidth="1.25" strokeDasharray="5 3" opacity="0.9" />
        ) : null}
        {showVol ? (
          <g>
            {vols.map((v, i) => {
              const bw = (W - 12) / vols.length;
              const bh = Math.max(2, (v / maxVol) * 30);
              return (
                <rect key={i} x={6 + i * bw + 1} y={H - bh - 4} width={Math.max(1, bw - 2)} height={bh} fill="#2a3448" rx="1" />
              );
            })}
          </g>
        ) : null}
      </svg>
      <figcaption className="mut" style={{ fontSize: "0.7rem", display: "flex", gap: "0.8rem", marginTop: "0.35rem" }}>
        <span className="lp-num">{timeframe} · Demo data</span>
        {showSMA ? <span style={{ color: "#56c8ff" }}>- - SMA overlay</span> : null}
        <span style={{ marginLeft: "auto" }} className="lp-num">
          {series[series.length - 1].toFixed(2)}
        </span>
      </figcaption>
    </figure>
  );
}

export { DemoChart as default };
