import React from "react";
import { ArrowRight } from "lucide-react";
import Reveal from "./Reveal.jsx";

/* PlainEnglishStory: MODEL OUTPUT -> PLAIN ENGLISH. Demonstrates the
 * complex→understandable translation that defines the product voice. */
function PlainEnglishStory() {
  return (
    <section className="lp-section" aria-labelledby="plain-h" style={{ paddingTop: 0 }}>
      <div className="landing-inner">
        <Reveal>
          <p className="lp-kicker">Plain English</p>
          <h2 className="lp-h2" id="plain-h">Complex in. Understandable out.</h2>
        </Reveal>
        <div className="lp-split" style={{ marginTop: "1.2rem" }}>
          <Reveal className="lp-panel-nested lp-pe-card">
            <p className="lp-label">Model output</p>
            <p className="lp-pe-model" style={{ marginTop: 8 }}>
              ret_q10(21d)=−0.07 · ret_q90(21d)=+0.10 · vol=normal<br />p_drop10(21d)=0.16 · p_up=0.58 (skill vs base rate −0.011)
            </p>
          </Reveal>
          <Reveal delay={140} className="lp-panel lp-pe-card" style={{ borderColor: "#3ddc84" }}>
            <p className="lp-label" style={{ color: "#3ddc84" }}>Plain English</p>
            <p className="lp-pe-say">&ldquo;Over 21 trading days this stock usually moves between −7% and +10%. The up/down lean has no proven edge.&rdquo;</p>
            <p className="mut" style={{ fontSize: "0.8rem", marginTop: 8, display: "flex", alignItems: "center", gap: 6 }}>
              Same math, human words <ArrowRight aria-hidden="true" style={{ width: 13, height: 13 }} /> with reasons you can check.
            </p>
          </Reveal>
        </div>
        <div className="lp-arrow" aria-hidden="true" style={{ display: "none" }} />
      </div>
    </section>
  );
}

export { PlainEnglishStory as default };
