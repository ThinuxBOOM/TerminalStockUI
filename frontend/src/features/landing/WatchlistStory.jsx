import React, { useState } from "react";
import { Link } from "react-router-dom";
import Reveal from "./Reveal.jsx";
import { DEMO_SYMBOLS, getDemoSymbol } from "./demo/demoData.js";

/* WatchlistStory: "KEEP THE SIGNALS YOU CARE ABOUT CLOSE." Rows animate in,
 * hover/focus expands to reveal forecast + market + freshness, and hovering
 * a row updates the adjacent viz panel. Demo-labeled throughout. */
const ORDER = ["AAPL", "NVDA", "TSLA", "ASML"];

function vizFor(symbol) {
  if (symbol === "ASML") {
    return { symbol: "ASML", name: "ASML Holding", price: "€682.40", change: "+0.9%", market: "Euronext Amsterdam (XAMS)", forecast: "58% ↑ 21D · MEDIUM", fresh: "FRESH · 31s ago" };
  }
  const d = getDemoSymbol(symbol);
  const f = d.forecast[21];
  return {
    symbol: d.symbol, name: d.name,
    price: `$${d.price.toFixed(2)}`, change: `${d.changePct >= 0 ? "+" : ""}${d.changePct.toFixed(2)}%`,
    market: `${d.market} (${d.mic})`, forecast: `${f.prob}% ${f.direction === "up" ? "↑" : "↓"} 21D · ${f.confidence}`,
    fresh: `${d.freshness} · ${d.updatedAgo}`,
  };
}

function WatchlistStory() {
  const [active, setActive] = useState("AAPL");
  const viz = vizFor(active);

  return (
    <section className="lp-section" aria-labelledby="watch-h" style={{ paddingTop: 0 }}>
      <div className="landing-inner">
        <Reveal>
          <p className="lp-kicker">Watchlists</p>
          <h2 className="lp-h2" id="watch-h">Keep the signals you care about close.</h2>
          <p className="lp-sub">Hover a row — the signal, market and freshness reveal themselves, and the panel follows.</p>
        </Reveal>
        <div className="lp-watch-grid">
          <Reveal className="lp-rows" delay={80}>
            <div style={{ display: "grid", gap: 8 }} role="list">
              {ORDER.map((sym) => {
                const d = sym === "ASML"
                  ? { symbol: "ASML", name: "ASML Holding", price: 682.4, changePct: 0.9 }
                  : getDemoSymbol(sym);
                const up = d.changePct >= 0;
                return (
                  <div
                    key={sym}
                    role="listitem"
                    tabIndex={0}
                    className={`lp-wrow${active === sym ? " is-active" : ""}`}
                    onMouseEnter={() => setActive(sym)}
                    onFocus={() => setActive(sym)}
                    aria-label={`${d.symbol} ${d.name}, demo row`}
                  >
                    <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
                      <span className="lp-num" style={{ fontWeight: 800 }}>{d.symbol}</span>
                      <span className="mut" style={{ fontSize: "0.75rem" }}>{d.name}</span>
                      <span className={`lp-num ${up ? "up" : "down"}`} style={{ marginLeft: "auto", fontWeight: 700, fontSize: "0.8rem" }}>
                        {up ? "+" : ""}{d.changePct.toFixed(2)}%
                      </span>
                    </div>
                    <div className="extra">
                      <p className="mut lp-num" style={{ fontSize: "0.72rem", margin: "6px 0 0" }}>
                        {vizFor(sym).forecast} · {vizFor(sym).market} · {vizFor(sym).fresh} · Demo data
                      </p>
                    </div>
                  </div>
                );
              })}
            </div>
          </Reveal>
          <Reveal delay={160} className="lp-panel" style={{ padding: "1.4rem" }}>
            <p className="lp-label">Signal preview · Demo data</p>
            <p className="lp-num" style={{ fontSize: "1.8rem", fontWeight: 800, margin: "6px 0 0" }}>
              {viz.symbol} <span className="mut" style={{ fontSize: "0.9rem", fontWeight: 400 }}>{viz.price}</span>
            </p>
            <p className={`lp-num ${String(viz.change).startsWith("-") ? "down" : "up"}`} style={{ fontWeight: 700 }}>{viz.change}</p>
            <div className="lp-prov-row"><span className="mut">Forecast</span><span className="lp-num">{viz.forecast}</span></div>
            <div className="lp-prov-row"><span className="mut">Market</span><span>{viz.market}</span></div>
            <div className="lp-prov-row" style={{ borderBottom: "1px solid #1c2433" }}><span className="mut">Freshness</span><span className="lp-fresh">● {viz.fresh}</span></div>
            <p style={{ marginTop: 12 }}>
              <Link to="/watchlist" className="lp-btn-ghost" style={{ fontSize: "0.8rem" }}>Open My List →</Link>
            </p>
          </Reveal>
        </div>
      </div>
    </section>
  );
}

export { WatchlistStory as default };
