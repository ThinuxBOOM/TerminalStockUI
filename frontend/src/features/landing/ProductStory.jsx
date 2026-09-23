import React from "react";
import { Link } from "react-router-dom";
import Reveal from "./Reveal.jsx";

const STEPS = [
  { n: "01", t: "Markets", d: "Six venues, one search box. NYSE to Shanghai without tab-switching.", to: "/search" },
  { n: "02", t: "Watchlists", d: "Keep the signals you care about close, saved in this browser.", to: "/watchlist" },
  { n: "03", t: "Forecasts", d: "1–21 day odds in plain words, with confidence and quality grades.", to: "/forecast/AAPL" },
  { n: "04", t: "Research", d: "Analytics, events, technicals, fundamentals — one brief per stock.", to: "/security/AAPL" },
  { n: "05", t: "Backtests", d: "Judge past calls honestly: wins and misses, before you trust.", to: "/backtest" },
];

/* ProductStory: "ONE TERMINAL. LESS TAB SWITCHING." — an animated
 * horizontal sequence (not 5 static cards): staggered reveal + progress
 * underline sweeps left→right, mirroring the real workflow order. */
function ProductStory() {
  return (
    <section className="lp-section" id="product" aria-labelledby="product-h">
      <div className="landing-inner">
        <span id="features" style={{ display: "block", height: 1 }} aria-hidden="true" />
        <Reveal>
          <p className="lp-kicker">Product</p>
          <h2 className="lp-h2" id="product-h">One terminal. Less tab switching.</h2>
          <p className="lp-sub">
            The whole loop — find it, understand the signal, compare, research deeper,
            follow it — lives in one dense workspace that feels instant.
          </p>
        </Reveal>
        <div className="lp-seq" role="list">
          {STEPS.map((s, i) => (
            <Reveal as="div" key={s.t} role="listitem" className="lp-panel lp-seq-step" delay={i * 110}>
              <span className="n lp-num">{s.n}</span>
              <h3>{s.t}</h3>
              <p>{s.d}</p>
              <Link to={s.to} className="lp-seq-link">Open {s.t.toLowerCase()} →</Link>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}

export { ProductStory as default };
