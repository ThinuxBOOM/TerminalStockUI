import React, { useEffect, useRef, useState } from "react";
import { Globe } from "lucide-react";
import Reveal from "./Reveal.jsx";

/* MarketsStory: multiple markets, one workspace. Sticky viz on desktop that
 * morphs as the reader moves through US → EUROPE → SRI LANKA groups (sticky
 * + section progress + transforms). Only lists venues the app supports:
 * XNYS / XNAS / XSHG / XPAR / XAMS / XBRU, with the XCOL-disabled note. */
const GROUPS = [
  {
    id: "us", kicker: "United States", title: "NYSE · Nasdaq — the deep end",
    body: "AAPL, NVDA, TSLA and thousands more. S&P 500, Nasdaq 100 and Dow breadth in the same view as your stock.",
    codes: [["XNYS", "New York"], ["XNAS", "Nasdaq"]],
  },
  {
    id: "eu", kicker: "Europe + Shanghai", title: "Paris · Amsterdam · Brussels · Shanghai",
    body: "ASML.AS, MC.PA and 600519.SS resolve in the same search box — suffixes included, no venue-hopping.",
    codes: [["XSHG", "Shanghai"], ["XPAR", "Paris"], ["XAMS", "Amsterdam"], ["XBRU", "Brussels"]],
  },
  {
    id: "lk", kicker: "Sri Lanka", title: "ASPI, in the same workspace",
    body: "Follow the Colombo story next to your US and European names. One list, every market you track.",
    codes: [],
    disabled: "XCOL (Colombo) is disabled in this build — the app resolves US, EU and Shanghai venues above.",
  },
];

function MarketsStory() {
  const sectionRef = useRef(null);
  const [active, setActive] = useState(0);

  useEffect(() => {
    let raf = 0;
    function update() {
      raf = 0;
      const el = sectionRef.current;
      if (!el) return;
      const groups = el.querySelectorAll("[data-mkt-group]");
      let best = 0;
      let bestTop = Infinity;
      groups.forEach((g, i) => {
        const r = g.getBoundingClientRect();
        const d = Math.abs(r.top - window.innerHeight * 0.4);
        if (d < bestTop) { bestTop = d; best = i; }
      });
      setActive(best);
    }
    function onScroll() { if (!raf) raf = requestAnimationFrame(update); }
    update();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => { window.removeEventListener("scroll", onScroll); if (raf) cancelAnimationFrame(raf); };
  }, []);

  const g = GROUPS[active];

  return (
    <section className="lp-section" id="markets" ref={sectionRef} aria-labelledby="markets-h" style={{ scrollMarginTop: 70 }}>
      <div className="landing-inner">
        <Reveal>
          <p className="lp-kicker">Markets</p>
          <h2 className="lp-h2" id="markets-h">Multiple markets. One workspace.</h2>
          <p className="lp-sub">From New York to Shanghai to Colombo — one search box, one list, one research trail.</p>
        </Reveal>
        <div className="lp-mkt-layout">
          <div className="lp-sticky">
            <div className="lp-panel lp-mkt-viz" aria-live="polite" aria-label={`Market focus: ${g.title}`}>
              <p className="lp-label"><Globe aria-hidden="true" style={{ width: 12, height: 12, display: "inline", verticalAlign: -1 }} /> {g.kicker}</p>
              <h3 style={{ margin: 0, fontSize: "1.25rem", fontWeight: 800 }}>{g.title}</h3>
              <p className="mut" style={{ margin: 0, fontSize: "0.85rem", lineHeight: 1.6 }}>{g.body}</p>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                {g.codes.map(([c, n]) => (
                  <span key={c} className="lp-code" title={n}>{c}</span>
                ))}
              </div>
              <div className="lp-meter" aria-hidden="true">
                <span style={{ width: `${((active + 1) / GROUPS.length) * 100}%` }} />
              </div>
            </div>
          </div>
          <div style={{ display: "grid", gap: 12 }}>
            {GROUPS.map((grp, i) => (
              <Reveal key={grp.id} data-mkt-group className="lp-panel lp-mkt-group" delay={0}>
                <h3>{i + 1}. {grp.title}</h3>
                <p className="mut" style={{ fontSize: "0.82rem", margin: "6px 0 0" }}>{grp.body}</p>
                {grp.codes.length ? (
                  <ul>
                    {grp.codes.map(([c, n]) => (
                      <li key={c}><span className="lp-code">{c}</span> <span style={{ marginLeft: 6 }}>{n}</span></li>
                    ))}
                  </ul>
                ) : null}
                {grp.disabled ? <p className="mut lp-disabled" style={{ fontSize: "0.75rem", marginTop: 8 }}>{grp.disabled}</p> : null}
              </Reveal>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

export { MarketsStory as default };
