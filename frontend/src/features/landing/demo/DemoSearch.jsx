import React from "react";
import { Search } from "lucide-react";
import { DEMO_SYMBOLS } from "./demoData.js";

/* Demo search: filters the three DEMO_SYMBOLS only. Same design language
 * as the terminal search (mono symbols, muted names, keyboard friendly). */
function DemoSearch({ query, onQuery, selected, onSelect }) {
  const q = query.trim().toUpperCase();
  const results = DEMO_SYMBOLS.filter(
    (d) => !q || d.symbol.includes(q) || d.name.toUpperCase().includes(q)
  );

  return (
    <div>
      <div style={{ position: "relative" }}>
        <Search aria-hidden="true" style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", width: 15, height: 15, color: "#8b94a7" }} />
        <input
          className="lp-search lp-num"
          value={query}
          onChange={(e) => onQuery(e.target.value)}
          placeholder="Search AAPL, NVDA, TSLA…"
          aria-label="Search demo symbols"
          spellCheck={false}
          autoComplete="off"
        />
      </div>
      <div role="listbox" aria-label="Demo results" style={{ display: "grid", gap: 4, marginTop: 8 }}>
        {results.map((d) => (
          <button
            key={d.symbol}
            type="button"
            role="option"
            aria-selected={d.symbol === selected}
            className="lp-result"
            onClick={() => onSelect(d.symbol)}
          >
            <span>
              <span className="lp-num" style={{ fontWeight: 800, fontSize: "0.85rem" }}>{d.symbol}</span>
              <span className="mut" style={{ fontSize: "0.72rem", marginLeft: 8 }}>{d.name}</span>
            </span>
            <span className={`lp-num ${d.changePct >= 0 ? "up" : "down"}`} style={{ fontSize: "0.75rem", fontWeight: 700 }}>
              {d.changePct >= 0 ? "+" : ""}{d.changePct.toFixed(2)}%
            </span>
          </button>
        ))}
        {results.length === 0 ? (
          <p className="mut" style={{ fontSize: "0.75rem", margin: "4px 2px" }}>
            Demo covers AAPL, NVDA and TSLA. Try one of those.
          </p>
        ) : null}
      </div>
    </div>
  );
}

export { DemoSearch as default };
