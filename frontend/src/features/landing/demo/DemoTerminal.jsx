import React, { useState } from "react";
import { DEMO_TIMEFRAMES, DEMO_INDICATORS, getDemoSymbol } from "./demoData.js";
import DemoSearch from "./DemoSearch.jsx";
import DemoChart from "./DemoChart.jsx";
import DemoForecast from "./DemoForecast.jsx";
import DemoWatchlist from "./DemoWatchlist.jsx";

/* DemoTerminal: the composed miniature terminal used by the landing demo.
 * Isolated useState only — same design language as the real terminal
 * (term colors, mono numerals, dense panels) but fully self-contained. */
function DemoTerminal() {
  const [symbol, setSymbol] = useState("AAPL");
  const [query, setQuery] = useState("");
  const [horizon, setHorizon] = useState(21);
  const [timeframe, setTimeframe] = useState("1M");
  const [indicators, setIndicators] = useState(["SMA20"]);
  const [watched, setWatched] = useState(["AAPL"]);

  const data = getDemoSymbol(symbol);

  function toggleIndicator(name) {
    setIndicators((prev) => (prev.includes(name) ? prev.filter((i) => i !== name) : [...prev, name]));
  }
  function toggleWatch(sym) {
    setWatched((prev) => (prev.includes(sym) ? prev.filter((s) => s !== sym) : [...prev, sym]));
  }
  function pick(sym) {
    setSymbol(sym);
    setQuery("");
  }

  const up = data.changePct >= 0;

  return (
    <div aria-label="Interactive demo terminal (demo data)">
      <div className="lp-demo-head">
        <span className="lp-num" style={{ fontWeight: 800, fontSize: "0.85rem" }}>
          {data.symbol} <span className="mut" style={{ fontWeight: 400 }}>· {data.name}</span>
        </span>
        <span className="lp-num" style={{ fontSize: "1.05rem", fontWeight: 800 }}>${data.price.toFixed(2)}</span>
        <span className={`lp-num ${up ? "up" : "down"}`} style={{ fontWeight: 700, fontSize: "0.8rem" }}>
          {up ? "▲" : "▼"} {up ? "+" : ""}{data.changePct.toFixed(2)}%
        </span>
        <span className="lp-demo-tag" style={{ marginLeft: "auto" }}>DEMO DATA</span>
      </div>
      <div className="lp-demo-grid">
        <div className="lp-demo-col">
          <DemoSearch query={query} onQuery={setQuery} selected={symbol} onSelect={pick} />
          <div style={{ marginTop: 12 }}>
            <DemoWatchlist watched={watched} onToggle={toggleWatch} />
          </div>
        </div>
        <div className="lp-demo-col">
          <div className="lp-chip-row" role="group" aria-label="Chart timeframe" style={{ marginBottom: 8 }}>
            {DEMO_TIMEFRAMES.map((t) => (
              <button key={t} type="button" className="lp-chip lp-num" aria-pressed={t === timeframe} onClick={() => setTimeframe(t)}>
                {t}
              </button>
            ))}
            <span style={{ width: 8 }} />
            {DEMO_INDICATORS.map((ind) => (
              <button key={ind} type="button" className="lp-chip lp-num" aria-pressed={indicators.includes(ind)} onClick={() => toggleIndicator(ind)}>
                {ind}
              </button>
            ))}
          </div>
          <DemoChart data={data} timeframe={timeframe} indicators={indicators} />
          <p className="mut" style={{ fontSize: "0.7rem", marginTop: 8 }}>
            {data.market} ({data.mic}) · {data.currency} · Source {data.source} · {data.updatedAgo}
          </p>
        </div>
        <div className="lp-demo-col">
          <DemoForecast data={data} horizon={horizon} onHorizon={setHorizon} />
        </div>
      </div>
    </div>
  );
}

export { DemoTerminal as default };
