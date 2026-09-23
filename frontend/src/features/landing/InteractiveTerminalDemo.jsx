import React from "react";
import { Link } from "react-router-dom";
import Reveal from "./Reveal.jsx";
import DemoTerminal from "./demo/DemoTerminal.jsx";

/* InteractiveTerminalDemo: the signature interaction. Fast, obvious,
 * purposeful — visitors search AAPL/NVDA/TSLA, flip horizons, timeframes,
 * indicators and the watchlist. Isolated demo state, labeled Demo data. */
function InteractiveTerminalDemo() {
  return (
    <section className="lp-section" id="demo" aria-labelledby="demo-h" style={{ scrollMarginTop: 70 }}>
      <div className="landing-inner">
        <Reveal>
          <p className="lp-kicker">Interactive demo</p>
          <h2 className="lp-h2" id="demo-h">Touch the terminal. It&rsquo;s real data-behavior, demo numbers.</h2>
          <p className="lp-sub">
            Search <b className="lp-num">AAPL</b>, <b className="lp-num">NVDA</b> or <b className="lp-num">TSLA</b>,
            switch the forecast horizon (1/7/14/21), change the chart timeframe, toggle indicators,
            and add to the watchlist. Everything below runs locally on demo data.
          </p>
        </Reveal>
        <Reveal delay={120}>
          <div className="lp-demo">
            <DemoTerminal />
          </div>
          <p style={{ marginTop: 10, display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
            <Link to="/app" className="lp-btn" style={{ fontSize: "0.78rem", padding: "0.6rem 1.1rem" }}>Open the live terminal →</Link>
            <span className="mut" style={{ fontSize: "0.75rem" }}>Same design language, live numbers inside.</span>
          </p>
        </Reveal>
      </div>
    </section>
  );
}

export { InteractiveTerminalDemo as default };
