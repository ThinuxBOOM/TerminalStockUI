import React from "react";
import { Link } from "react-router-dom";
import { ArrowRight, Zap } from "lucide-react";
import Reveal from "./Reveal.jsx";

const FAQS = [
  { q: "Do I need to know investing words?", a: "No. Everything is written in plain words — up, down, and why. If a chart looks complex, read the one-sentence summary above it." },
  { q: "Is this telling me what to buy?", a: "No. OneMarket gives you a shortlist to research further — not orders and not financial advice. Always do your own research." },
  { q: "How are forecasts made?", a: "A deterministic engine (statistical baselines plus small machine-learning models) estimates, for each horizon, the likely price range, the volatility regime and the chance of a large drop. It also gives an up/down lean, but in walk-forward tests that lean has not beaten a simple baseline, so the app labels it experimental and shows its measured record next to it." },
  { q: "Where do the numbers come from?", a: "Live market feeds. Each number shows its source, time, and freshness (live, delayed, closed, or stale) — so you can trust what you see." },
  { q: "Do I need an account?", a: "Yes. The terminal is private to members of this server. Sign in with the account your administrator created for you." },
  { q: "Which markets can I look up?", a: "One search covers NYSE (XNYS), Nasdaq (XNAS), Shanghai (XSHG), Paris (XPAR), Amsterdam (XAMS) and Brussels (XBRU). Try AAPL, 600519.SS or ASML.AS." },
];

/* FinalCTA: FAQ anchor (#faq) + the closing CTA "READY TO EXPLORE THE MARKET DIFFERENTLY?" with a
 * darkening backdrop of terminal UI. Background fades via CSS only. */
function FinalCTA() {
  return (
    <>
      <section className="lp-section" id="faq" aria-labelledby="faq-h" style={{ scrollMarginTop: 70, paddingTop: 0 }}>
        <div className="landing-inner">
          <Reveal>
            <p className="lp-kicker">Questions</p>
            <h2 className="lp-h2" id="faq-h">Simple answers.</h2>
          </Reveal>
          <div className="lp-faq">
            {FAQS.map((f, i) => (
              <Reveal key={f.q} delay={Math.min(i, 3) * 60}>
                <details>
                  <summary>{f.q}</summary>
                  <p>{f.a}</p>
                </details>
              </Reveal>
            ))}
          </div>
        </div>
      </section>

      <section className="lp-section" aria-labelledby="cta-h" style={{ paddingTop: 0 }}>
        <div className="landing-inner">
          <Reveal className="lp-cta">
            <svg className="lp-cta-bg" viewBox="0 0 1200 400" preserveAspectRatio="none" aria-hidden="true">
              <path d="M0,300 L200,280 L400,295 L600,250 L800,265 L1000,220 L1200,235" fill="none" stroke="#1c2433" strokeWidth="2" />
              <path d="M0,340 L200,330 L400,340 L600,310 L800,320 L1000,290 L1200,300" fill="none" stroke="#1c4a35" strokeWidth="2" opacity="0.7" />
              <rect x="120" y="60" width="220" height="90" rx="8" fill="none" stroke="#2a3448" />
              <rect x="860" y="80" width="220" height="90" rx="8" fill="none" stroke="#2a3448" />
              <text x="140" y="100" fill="#3ddc84" fontSize="26" fontFamily="monospace" opacity="0.5" className="lp-num">$193.42</text>
              <text x="880" y="120" fill="#3ddc84" fontSize="26" fontFamily="monospace" opacity="0.5" className="lp-num">−7% / +10%</text>
            </svg>
            <div style={{ position: "relative" }}>
              <p className="lp-kicker" style={{ justifyContent: "center" }}><Zap aria-hidden="true" style={{ width: 13, height: 13 }} /> Every number shows its source and age</p>
              <h2 id="cta-h">Ready to explore the market differently?</h2>
              <p className="mut" style={{ maxWidth: "34rem", margin: "12px auto 0", fontSize: "0.92rem", lineHeight: 1.65 }}>
                Open the terminal and look up your first stock. Deterministic analytics are the
                source of truth — every number shows its source and age.
              </p>
              <p style={{ marginTop: 22, display: "flex", gap: 10, justifyContent: "center", flexWrap: "wrap" }}>
                <Link to="/login" className="lp-btn">Sign in <ArrowRight aria-hidden="true" style={{ width: 15, height: 15 }} /></Link>
              </p>
              <p className="mut" style={{ fontSize: "0.7rem", marginTop: 14 }}>Not investment advice. For learning and research only.</p>
            </div>
          </Reveal>
        </div>
      </section>
    </>
  );
}

export { FinalCTA as default };
