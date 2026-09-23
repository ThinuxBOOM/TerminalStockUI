import React from "react";
import { Link } from "react-router-dom";

/* Living miniature terminal for the hero: AAPL $193.42 +1.42% with an SVG
 * sparkline and a 21D 64%↑ meter. Subtle CSS-only motion (live dot, line
 * draw, meter fill) — no JS timers, respects reduced motion via CSS. */
function HeroTerminal() {
  const spark = "M4,52 L20,48 L36,50 L52,44 L68,46 L84,40 L100,42 L116,36 L132,38 L148,30 L164,33 L180,26 L196,28 L212,20 L228,23 L236,16";
  return (
    <div className="lp-term" id="hero-terminal" role="img" aria-label="Preview of the OneMarket terminal: Apple at 193 dollars 42 cents, up 1.42 percent, 21-day forecast 64 percent chance of rising">
      <div className="lp-term-bar" aria-hidden="true">
        <span className="dot" style={{ background: "#ff5c5c" }} />
        <span className="dot" style={{ background: "#ffb454" }} />
        <span className="dot" style={{ background: "#3ddc84" }} />
        <span className="lp-num mut" style={{ fontSize: "0.68rem", marginLeft: 8 }}>/security/AAPL — DEMO DATA</span>
        <span className="lp-live" style={{ marginLeft: "auto" }}><i />LIVE FEEL</span>
      </div>
      <div className="lp-term-body">
        <p className="lp-label">AAPL · Apple Inc. · NASDAQ</p>
        <p style={{ margin: "6px 0 0", display: "flex", alignItems: "baseline", gap: 10 }}>
          <span className="lp-num lp-price-tick" style={{ fontSize: "2rem", fontWeight: 800 }}>$193.42</span>
          <span className="lp-num up" style={{ fontWeight: 700, fontSize: "0.9rem" }}>+1.42%</span>
        </p>
        <svg className="lp-spark" viewBox="0 0 240 64" width="100%" height="64" aria-hidden="true">
          <defs>
            <linearGradient id="hero-spark-fill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#3ddc84" stopOpacity="0.25" />
              <stop offset="100%" stopColor="#3ddc84" stopOpacity="0" />
            </linearGradient>
          </defs>
          <path d={`${spark} L236,64 L4,64 Z`} fill="url(#hero-spark-fill)" />
          <path className="line" d={spark} fill="none" stroke="#3ddc84" strokeWidth="2" strokeLinejoin="round" />
        </svg>
        <div className="lp-panel-nested" style={{ marginTop: 10, padding: "8px 10px", display: "flex", alignItems: "center", gap: 10 }}>
          <span className="lp-num" style={{ fontSize: "0.72rem", color: "#8b94a7" }}>21D</span>
          <span className="lp-num" style={{ fontSize: "1.05rem", fontWeight: 800, color: "#3ddc84" }}>64% ↑</span>
          <div className="lp-meter" style={{ flex: 1 }} aria-hidden="true"><span style={{ width: "64%" }} /></div>
          <span className="lp-num" style={{ fontSize: "0.68rem", color: "#8b94a7" }}>HIGH</span>
        </div>
        <p className="mut" style={{ fontSize: "0.7rem", margin: "8px 0 0" }}>
          Source Demo feed · updated 12s ago · <span className="up" style={{ fontWeight: 700 }}>● FRESH</span>
        </p>
      </div>
    </div>
  );
}

export { HeroTerminal as default };
