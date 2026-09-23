import React from "react";
import { Link } from "react-router-dom";
import { ArrowRight, Check, Zap } from "lucide-react";
import Reveal from "./Reveal.jsx";

const FAQS = [
  { q: "Do I need to know investing words?", a: "No. Everything is written in plain words — up, down, and why. If a chart looks complex, read the one-sentence summary above it." },
  { q: "Is this telling me what to buy?", a: "No. OneMarket gives you a shortlist to research further — not orders and not financial advice. Always do your own research." },
  { q: "How are forecasts made?", a: "Math decides. A deterministic engine is the source of truth; probabilities come with confidence and quality grades, wins and misses included." },
  { q: "Where do the numbers come from?", a: "Live market feeds. Each number shows its source, time, and freshness (live, delayed, closed, or stale) — so you can trust what you see." },
  { q: "Is anything locked or paid right now?", a: "Nothing is locked. The plans below preview where subscriptions are heading in V2 — today everything is free to explore, no account needed." },
  { q: "Which markets can I look up?", a: "One search covers NYSE (XNYS), Nasdaq (XNAS), Shanghai (XSHG), Paris (XPAR), Amsterdam (XAMS) and Brussels (XBRU). Try AAPL, 600519.SS or ASML.AS." },
];

/* FinalCTA: pricing anchor (#pricing, reference-only) + FAQ anchor (#faq) +
 * the closing CTA "READY TO EXPLORE THE MARKET DIFFERENTLY?" with a
 * darkening backdrop of terminal UI. Background fades via CSS only. */
function FinalCTA() {
  return (
    <>
      <section className="lp-section" id="pricing" aria-labelledby="pricing-h" style={{ scrollMarginTop: 70 }}>
        <div className="landing-inner">
          <Reveal>
            <p className="lp-kicker">Pricing · Reference only</p>
            <h2 className="lp-h2" id="pricing-h">Free to explore. Everything open.</h2>
            <p className="lp-sub">
              Nothing is locked right now — no account, no paywall. These plans preview V2.{" "}
              <Link to="/pricing" style={{ color: "#3ddc84", fontWeight: 700 }}>See live plans →</Link>
            </p>
          </Reveal>
          <div className="lp-plans">
            <Reveal className="lp-panel-nested lp-plan">
              <h3 style={{ margin: 0 }}>Starter</h3>
              <p className="lp-num" style={{ fontSize: "1.6rem", fontWeight: 800, margin: "6px 0 0" }}>$0 <span className="mut" style={{ fontSize: "0.7rem", fontWeight: 400 }}>free forever</span></p>
              <ul>
                <li><Check aria-hidden="true" style={{ width: 13, height: 13, color: "#3ddc84", flexShrink: 0 }} />Search every market</li>
                <li><Check aria-hidden="true" style={{ width: 13, height: 13, color: "#3ddc84", flexShrink: 0 }} />Plain-words forecasts + charts</li>
                <li><Check aria-hidden="true" style={{ width: 13, height: 13, color: "#3ddc84", flexShrink: 0 }} />My List watchlist</li>
              </ul>
              <p style={{ marginTop: 14 }}><Link to="/app" className="lp-btn" style={{ fontSize: "0.78rem" }}>Start exploring →</Link></p>
            </Reveal>
            <Reveal delay={100} className="lp-panel lp-plan" style={{ borderColor: "#3ddc84" }}>
              <h3 style={{ margin: 0, color: "#3ddc84" }}>Pro <span className="lp-demo-tag">SOON</span></h3>
              <p className="lp-num" style={{ fontSize: "1.6rem", fontWeight: 800, margin: "6px 0 0" }}>$— <span className="mut" style={{ fontSize: "0.7rem", fontWeight: 400 }}>V2</span></p>
              <ul>
                <li><Check aria-hidden="true" style={{ width: 13, height: 13, color: "#3ddc84", flexShrink: 0 }} />Everything in Starter</li>
                <li><Check aria-hidden="true" style={{ width: 13, height: 13, color: "#3ddc84", flexShrink: 0 }} />Deeper research reports</li>
                <li><Check aria-hidden="true" style={{ width: 13, height: 13, color: "#3ddc84", flexShrink: 0 }} />Bigger watchlists + alerts</li>
              </ul>
              <p style={{ marginTop: 14 }}><span className="mut" style={{ fontSize: "0.75rem" }}>Coming soon — nothing is gated today.</span></p>
            </Reveal>
            <Reveal delay={180} className="lp-panel-nested lp-plan">
              <h3 style={{ margin: 0 }}>Team</h3>
              <p className="lp-num" style={{ fontSize: "1.6rem", fontWeight: 800, margin: "6px 0 0" }}>$— <span className="mut" style={{ fontSize: "0.7rem", fontWeight: 400 }}>V2</span></p>
              <ul>
                <li><Check aria-hidden="true" style={{ width: 13, height: 13, color: "#3ddc84", flexShrink: 0 }} />Everything in Pro</li>
                <li><Check aria-hidden="true" style={{ width: 13, height: 13, color: "#3ddc84", flexShrink: 0 }} />Shared lists + workspaces</li>
                <li><Check aria-hidden="true" style={{ width: 13, height: 13, color: "#3ddc84", flexShrink: 0 }} />Priority help</li>
              </ul>
              <p style={{ marginTop: 14 }}><span className="mut" style={{ fontSize: "0.75rem" }}>Coming soon — nothing is gated today.</span></p>
            </Reveal>
          </div>
        </div>
      </section>

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
              <text x="880" y="120" fill="#3ddc84" fontSize="26" fontFamily="monospace" opacity="0.5" className="lp-num">64% ↑</text>
            </svg>
            <div style={{ position: "relative" }}>
              <p className="lp-kicker" style={{ justifyContent: "center" }}><Zap aria-hidden="true" style={{ width: 13, height: 13 }} /> Free · No account · No complicated setup</p>
              <h2 id="cta-h">Ready to explore the market differently?</h2>
              <p className="mut" style={{ maxWidth: "34rem", margin: "12px auto 0", fontSize: "0.92rem", lineHeight: 1.65 }}>
                Open the terminal and look up your first stock. Deterministic analytics are the
                source of truth — every number shows its source and age.
              </p>
              <p style={{ marginTop: 22, display: "flex", gap: 10, justifyContent: "center", flexWrap: "wrap" }}>
                <Link to="/app" className="lp-btn">Open Terminal <ArrowRight aria-hidden="true" style={{ width: 15, height: 15 }} /></Link>
                <Link to="/screener" className="lp-btn-ghost">See Top Picks</Link>
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
