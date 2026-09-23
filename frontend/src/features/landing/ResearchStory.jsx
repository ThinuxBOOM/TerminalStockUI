import React from "react";
import { Link } from "react-router-dom";
import { TrendingUp, SlidersHorizontal, Clock, Activity, Database, ShieldCheck, Zap, FlaskConical } from "lucide-react";
import Reveal from "./Reveal.jsx";

const ROWS = [
  { title: "Forecast", d: "1–21 day odds with confidence and quality.", to: "/forecast/AAPL", Icon: TrendingUp },
  { title: "Analytics", d: "Drivers behind the lean: trend, momentum, volatility.", to: "/forecast/AAPL", Icon: SlidersHorizontal },
  { title: "Events", d: "Earnings and dividends on the same timeline.", to: "/security/AAPL", Icon: Clock },
  { title: "Technical", d: "Overlays and levels on the chart, explained.", to: "/security/AAPL", Icon: Activity },
  { title: "Fundamentals", d: "Revenue, margins and the story in numbers.", to: "/security/AAPL", Icon: Database },
  { title: "Quality", d: "Data grade on every input — A means checkable.", to: "/providers", Icon: ShieldCheck },
  { title: "Valuation", d: "Multiples with context, not decoration.", to: "/security/AAPL", Icon: Zap },
  { title: "Backtest", d: "How calls like this turned out before.", to: "/backtest", Icon: FlaskConical },
];

/* ResearchStory: "WHEN YOU NEED MORE THAN A NUMBER." Progressive reveal
 * mirroring the app hierarchy: Forecast → Analytics → Events → Technical →
 * Fundamentals → Quality → Valuation → Backtest. */
function ResearchStory() {
  return (
    <section className="lp-section" id="research" aria-labelledby="research-h" style={{ scrollMarginTop: 70 }}>
      <div className="landing-inner">
        <Reveal>
          <p className="lp-kicker">Research</p>
          <h2 className="lp-h2" id="research-h">When you need more than a number.</h2>
          <p className="lp-sub">One Security Brief per stock, layered exactly the way analysts read — from signal down to receipts.</p>
        </Reveal>
        <div className="lp-research-grid">
          {ROWS.map((row, i) => (
            <Reveal key={row.title} className="lp-panel lp-rstep" delay={(i % 4) * 90}>
              <span className="lp-num mut" style={{ fontSize: "0.68rem" }}>{String(i + 1).padStart(2, "0")}</span>
              <h3><row.Icon aria-hidden="true" style={{ width: 14, height: 14, display: "inline", verticalAlign: -2, color: "#3ddc84" }} /> {row.title}</h3>
              <p>{row.d}</p>
              <Link to={row.to} className="lp-seq-link" aria-label={`Open ${row.title} example`}>Open →</Link>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}

export { ResearchStory as default };
