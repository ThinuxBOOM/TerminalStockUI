import React from "react";
import { Plus, Check } from "lucide-react";
import { DEMO_SYMBOLS } from "./demoData.js";

/* Demo watchlist: add/remove the three demo symbols. Isolated state —
 * nothing persists, nothing touches the real watchlist. */
function DemoWatchlist({ watched, onToggle }) {
  return (
    <div>
      <p className="lp-label">Watchlist · Demo data</p>
      <div style={{ display: "grid", gap: 6, marginTop: 8 }}>
        {DEMO_SYMBOLS.map((d) => {
          const on = watched.includes(d.symbol);
          return (
            <div key={d.symbol} className="lp-panel-nested" style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 8px" }}>
              <span className="lp-num" style={{ fontWeight: 800, fontSize: "0.8rem" }}>{d.symbol}</span>
              <span className={`lp-num ${d.changePct >= 0 ? "up" : "down"}`} style={{ fontSize: "0.72rem" }}>
                {d.changePct >= 0 ? "+" : ""}{d.changePct.toFixed(2)}%
              </span>
              <button
                type="button"
                className="lp-watch-btn"
                aria-pressed={on}
                aria-label={on ? `Remove ${d.symbol} from demo watchlist` : `Add ${d.symbol} to demo watchlist`}
                onClick={() => onToggle(d.symbol)}
                style={{ marginLeft: "auto" }}
              >
                {on ? <Check aria-hidden="true" style={{ width: 12, height: 12 }} /> : <Plus aria-hidden="true" style={{ width: 12, height: 12 }} />}
                {on ? "WATCHING" : "WATCH"}
              </button>
            </div>
          );
        })}
      </div>
      <p className="mut" style={{ fontSize: "0.7rem", marginTop: 8 }}>
        {watched.length === 0 ? "Tap WATCH to follow a signal." : `Following ${watched.join(", ")} in this demo.`}
      </p>
    </div>
  );
}

export { DemoWatchlist as default };
