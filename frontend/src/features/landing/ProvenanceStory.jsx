import React, { useState } from "react";
import { Link } from "react-router-dom";
import Reveal from "./Reveal.jsx";

const ROWS = [
  ["Source", "Demo feed (live: Alpaca / Stooq)"],
  ["Timestamp", "Updated 12s ago"],
  ["Market", "NASDAQ (XNAS)"],
  ["Currency", "USD"],
  ["Freshness", "● FRESH"],
  ["Quality", "A — checkable"],
];

/* ProvenanceStory: "KNOW WHERE THE NUMBER CAME FROM." $193.42 with source,
 * timestamp, market, currency and freshness; click/hover reveals each field
 * and metadata animates in. Reuses the terminal provenance language. */
function ProvenanceStory() {
  const [open, setOpen] = useState(false);
  return (
    <section className="lp-section" aria-labelledby="prov-h" style={{ paddingTop: 0 }}>
      <div className="landing-inner lp-prov-grid">
        <Reveal>
          <p className="lp-kicker">Provenance</p>
          <h2 className="lp-h2" id="prov-h">Know where the number came from.</h2>
          <p className="lp-sub">
            Every price carries its receipts: source, timestamp, market, currency and freshness.
            If a feed is down we say so — we never fabricate a live value.
          </p>
          <p style={{ marginTop: 12 }}>
            <Link to="/providers" className="lp-btn-ghost" style={{ fontSize: "0.8rem" }}>Check data health →</Link>
          </p>
        </Reveal>
        <Reveal delay={120} className="lp-panel lp-prov-ticket">
          <p className="lp-label">AAPL · Demo data</p>
          <p style={{ display: "flex", alignItems: "baseline", gap: 10, margin: "6px 0 0" }}>
            <span className="lp-num" style={{ fontSize: "2.2rem", fontWeight: 800 }}>$193.42</span>
            <span className="lp-fresh">● FRESH</span>
          </p>
          <button
            type="button"
            className="lp-btn-ghost"
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
            style={{ fontSize: "0.75rem", marginTop: 10, padding: "0.45rem 0.8rem" }}
          >
            {open ? "Hide provenance ▲" : "Reveal provenance ▼"}
          </button>
          <div style={{ display: "grid", marginTop: open ? 8 : 0 }}>
            {ROWS.map(([k, v], i) => (
              <div
                key={k}
                className="lp-prov-row"
                style={{
                  opacity: open ? 1 : 0,
                  transform: open ? "none" : "translateY(8px)",
                  transition: `opacity 300ms ease ${i * 70}ms, transform 300ms ease ${i * 70}ms`,
                }}
                aria-hidden={open ? "false" : "true"}
              >
                <span className="mut">{k}</span>
                <span className="lp-num" style={{ fontWeight: 600 }}>{v}</span>
              </div>
            ))}
          </div>
        </Reveal>
      </div>
    </section>
  );
}

export { ProvenanceStory as default };
