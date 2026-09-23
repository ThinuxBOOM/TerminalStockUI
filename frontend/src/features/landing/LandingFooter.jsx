import React from "react";
import { Link } from "react-router-dom";

/* LandingFooter: ONEMARKET TERMINAL columns Product / Markets / Research /
 * Guide + Account / Legal. Reuses the canonical legal copy — no duplicate
 * conflicting text. */
function LandingFooter() {
  return (
    <footer className="lp-footer" role="contentinfo">
      <div className="landing-inner">
        <div className="lp-footer-grid">
          <div>
            <p style={{ fontWeight: 900, letterSpacing: "-0.01em", margin: 0 }}>
              ONE<span style={{ color: "#3ddc84" }}>MARKET</span> <span className="mut" style={{ fontWeight: 400, fontSize: "0.75rem" }}>TERMINAL</span>
            </p>
            <p className="mut" style={{ fontSize: "0.8rem", lineHeight: 1.65, marginTop: 8, maxWidth: "22rem" }}>
              The stock terminal for people who want to understand the market.
              Research stocks. Understand forecasts. Follow the data.
            </p>
          </div>
          <nav aria-label="Product">
            <h3>PRODUCT</h3>
            <Link to="/app">Open Terminal</Link>
            <Link to="/screener">Top Picks</Link>
            <Link to="/watchlist">My List</Link>
            <a href="#demo">Interactive Demo</a>
          </nav>
          <nav aria-label="Markets and research">
            <h3>MARKETS · RESEARCH</h3>
            <a href="#markets">Markets</a>
            <a href="#research">Research</a>
            <Link to="/security/AAPL">Security Brief</Link>
            <Link to="/backtest">Backtest Lab</Link>
          </nav>
          <nav aria-label="Guide and legal">
            <h3>GUIDE · LEGAL</h3>
            <a href="#how">How It Works</a>
            <a href="#pricing">Pricing</a>
            <a href="#faq">FAQ</a>
            <Link to="/providers">Data Health</Link>
          </nav>
        </div>
        <div style={{ display: "flex", gap: 16, flexWrap: "wrap", marginTop: 20, fontSize: "0.78rem" }}>
          <Link to="/login" style={{ color: "#8b94a7" }}>Sign In</Link>
          <Link to="/pricing" style={{ color: "#8b94a7" }}>Pricing</Link>
          <Link to="/providers" style={{ color: "#8b94a7" }}>Disclaimer</Link>
          <Link to="/providers" style={{ color: "#8b94a7" }}>Privacy</Link>
          <Link to="/providers" style={{ color: "#8b94a7" }}>Terms</Link>
        </div>
        <p className="lp-legal">
          Plain-English stock insights — no jargon needed. Numbers show their source and freshness.
          AI opinions are bounded and capped at 20%. Not investment advice.
        </p>
      </div>
    </footer>
  );
}

export { LandingFooter as default };
