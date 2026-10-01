import React from "react";
import { DEMO_HORIZONS, demoRange, formatDemoRange } from "./demoData.js";

/* Demo forecast card: horizon switch (1/7/14/21), the return range first,
 * then the experimental up/down lean with its measured record, as in the
 * app (components/ForecastOutlook). */
function DemoForecast({ data, horizon, onHorizon }) {
  const f = data.forecast[horizon] || data.forecast[21];
  const range = demoRange(data, horizon);

  return (
    <div>
      <p className="lp-label">Forecast · Demo data</p>
      <div className="lp-chip-row" role="group" aria-label="Forecast horizon" style={{ marginTop: 8 }}>
        {DEMO_HORIZONS.map((h) => (
          <button
            key={h}
            type="button"
            className="lp-chip lp-num"
            aria-pressed={h === horizon}
            onClick={() => onHorizon(h)}
          >
            {h}D
          </button>
        ))}
      </div>
      <p style={{ fontSize: "0.75rem", color: "#8b94a7", margin: "10px 0 0" }}>
        {horizon}-day range · 80% of comparable past periods
      </p>
      <p className="lp-num" style={{ fontSize: "2rem", fontWeight: 800, margin: "2px 0 0" }}>
        {formatDemoRange(range)}
      </p>
      <p style={{ fontSize: "0.82rem", lineHeight: 1.6, margin: "8px 0 0" }}>
        How far {data.symbol} has typically moved over {horizon} trading days. A guide to risk, not to direction.
      </p>
      <p className="mut" style={{ fontSize: "0.72rem", marginTop: 8 }}>
        Direction lean {f.prob}% {f.direction === "up" ? "↑" : "↓"} · experimental: in testing it has not beaten a simple baseline, and the app says so next to every lean.
      </p>
    </div>
  );
}

export { DemoForecast as default };
