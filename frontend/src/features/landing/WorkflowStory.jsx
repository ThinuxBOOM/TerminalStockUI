import React, { useState } from "react";
import { Link } from "react-router-dom";
import { Search, Eye, SlidersHorizontal, FileText, Star } from "lucide-react";
import Reveal from "./Reveal.jsx";

const STEPS = [
  { id: "SEARCH", Icon: Search, t: "Search", d: "One box, six markets. AAPL, 600519.SS, ASML.AS — all resolve.", to: "/search", cta: "Try search" },
  { id: "UNDERSTAND", Icon: Eye, t: "Understand", d: "Up or down in plain words, with the probability attached.", to: "/forecast/AAPL", cta: "See a forecast" },
  { id: "COMPARE", Icon: SlidersHorizontal, t: "Compare", d: "Top Picks ranks ideas by the math — not hype.", to: "/screener", cta: "Browse picks" },
  { id: "RESEARCH", Icon: FileText, t: "Research", d: "One brief per stock: chart, story and full trail.", to: "/security/AAPL", cta: "Open a brief" },
  { id: "FOLLOW", Icon: Star, t: "Follow", d: "One tap to My List. Check back any time.", to: "/watchlist", cta: "Open My List" },
];

/* WorkflowStory (anchor #how): SEARCH → UNDERSTAND → COMPARE → RESEARCH →
 * FOLLOW. Each step activates a miniature preview — a conceptual summary of
 * the 30-second loop, not a tutorial. */
function WorkflowStory() {
  const [active, setActive] = useState("UNDERSTAND");
  const current = STEPS.find((s) => s.id === active) || STEPS[0];

  return (
    <section className="lp-section" id="how" aria-labelledby="how-h" style={{ scrollMarginTop: 70 }}>
      <div className="landing-inner">
        <Reveal>
          <p className="lp-kicker">Workflow · 30 seconds</p>
          <h2 className="lp-h2" id="how-h">Search. Understand. Compare. Research. Follow.</h2>
          <p className="lp-sub">Five moves, one loop. Select a step to preview what happens.</p>
        </Reveal>
        <Reveal delay={100}>
          <div className="lp-flow" role="tablist" aria-label="Terminal workflow">
            {STEPS.map((s, i) => (
              <button
                key={s.id}
                type="button"
                role="tab"
                aria-selected={active === s.id}
                className="lp-fstep"
                onClick={() => setActive(s.id)}
                onMouseEnter={() => setActive(s.id)}
                onFocus={() => setActive(s.id)}
              >
                <span className="lp-num" style={{ fontSize: "0.68rem", color: "#3ddc84", fontWeight: 700 }}>{i + 1} · {s.id}</span>
                <span style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 4, fontWeight: 800 }}>
                  <s.Icon aria-hidden="true" style={{ width: 15, height: 15, color: "#3ddc84" }} /> {s.t}
                </span>
              </button>
            ))}
          </div>
          <div className="lp-panel lp-fpreview" aria-live="polite">
            <current.Icon aria-hidden="true" style={{ width: 22, height: 22, color: "#3ddc84", flexShrink: 0 }} />
            <div>
              <p style={{ margin: 0, fontWeight: 800 }}>{current.t} <span className="lp-num mut" style={{ fontWeight: 400, fontSize: "0.72rem" }}>STEP {STEPS.indexOf(current) + 1}/5</span></p>
              <p className="mut" style={{ margin: "4px 0 8px", fontSize: "0.85rem" }}>{current.d}</p>
              <Link to={current.to} className="lp-seq-link">{current.cta} →</Link>
            </div>
          </div>
        </Reveal>
      </div>
    </section>
  );
}

export { WorkflowStory as default };
