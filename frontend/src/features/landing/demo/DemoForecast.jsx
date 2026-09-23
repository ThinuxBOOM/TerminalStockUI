import React from "react";
import { DEMO_HORIZONS } from "./demoData.js";

/* Demo forecast card: horizon switch (1/7/14/21) + probability meter +
 * plain-English line. Never implies a guarantee — copy stays probabilistic. */
function DemoForecast({ data, horizon, onHorizon }) {
  const f = data.forecast[horizon] || data.forecast[21];
  const up = f.direction === "up";

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
      <p className="lp-num" style={{ fontSize: "2rem", fontWeight: 800, margin: "10px 0 0", color: up ? "#3ddc84" : "#ffb454" }}>
        {f.prob}% <span style={{ fontSize: "0.8rem", color: "#8b94a7" }}>{up ? "↑" : "↓"} {horizon}D</span>
      </p>
      <p style={{ fontSize: "0.75rem", color: "#8b94a7", margin: "2px 0 8px" }}>
        Chance of rising · Confidence {f.confidence} · Quality {f.quality}
      </p>
      <div className="lp-meter" aria-hidden="true">
        <span style={{ width: `${f.prob}%`, background: up ? "#3ddc84" : "#ffb454" }} />
      </div>
      <p style={{ fontSize: "0.82rem", lineHeight: 1.6, margin: "10px 0 0" }}>{f.text}</p>
      <p className="mut" style={{ fontSize: "0.68rem", marginTop: 8 }}>
        Model {f.model} · probabilities, not promises.
      </p>
    </div>
  );
}

export { DemoForecast as default };
