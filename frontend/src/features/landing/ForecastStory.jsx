import React, { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import Reveal from "./Reveal.jsx";
import { useCountUp } from "./useCountUp.js";

/* ForecastStory: "DON'T JUST SEE THE PRICE. UNDERSTAND THE SIGNAL."
 * 21D 64% CHANCE OF RISING, count-up 0->64 once on enter, then
 * Confidence/Quality/Model reveal sequentially + plain-English line.
 * Never implies a guarantee. */
function ForecastStory() {
  const ref = useRef(null);
  const [entered, setEntered] = useState(false);
  const value = useCountUp(64, entered, 1100);

  useEffect(() => {
    const el = ref.current;
    if (!el || typeof IntersectionObserver === "undefined") {
      setEntered(true);
      return undefined;
    }
    const io = new IntersectionObserver(
      (es) => es.forEach((e) => { if (e.isIntersecting) { setEntered(true); io.disconnect(); } }),
      { threshold: 0.35 }
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  const steps = [
    { k: "CONFIDENCE", v: "HIGH" },
    { k: "DATA QUALITY", v: "A" },
    { k: "MODEL", v: "ENSEMBLE v3" },
  ];

  return (
    <section className="lp-section" aria-labelledby="forecast-h">
      <div className="landing-inner lp-split">
        <Reveal>
          <p className="lp-kicker">Forecasts</p>
          <h2 className="lp-h2" id="forecast-h">Don&rsquo;t just see the price. Understand the signal.</h2>
          <p className="lp-sub">
            Every forecast is a probability with its workings shown — confidence, data quality
            and the model behind it. A 64% chance means it could still fall 36 times out of 100.
            That honesty is the feature.
          </p>
          <p style={{ marginTop: 12 }}>
            <Link to="/forecast/AAPL" className="lp-btn-ghost" style={{ fontSize: "0.8rem" }}>See a full forecast →</Link>
          </p>
        </Reveal>
        <div ref={ref} className="lp-panel" style={{ padding: "1.6rem" }} aria-label="Forecast example: 21-day, 64 percent chance of rising (demo data)">
          <p className="lp-label">21D · Chance of rising · Demo data</p>
          <p className="lp-big-num" aria-live="polite"><span className="lp-num">{value}</span>%</p>
          <div className="lp-meter" aria-hidden="true"><span style={{ width: `${value}%` }} /></div>
          <div className="lp-meta-row">
            {steps.map((s, i) => (
              <div key={s.k} className={`lp-meta${entered ? " is-on" : ""}`} style={{ transitionDelay: `${500 + i * 220}ms` }}>
                <p className="lp-label" style={{ fontSize: "0.6rem" }}>{s.k}</p>
                <p className="lp-num" style={{ fontWeight: 800, margin: "2px 0 0" }}>{s.v}</p>
              </div>
            ))}
          </div>
          <p style={{ fontSize: "0.85rem", lineHeight: 1.6, margin: "14px 0 0", opacity: entered ? 1 : 0, transition: "opacity 400ms ease 1150ms" }}>
            Models currently lean positive over the next 21 trading days — a signal to research further, never a promise.
          </p>
        </div>
      </div>
    </section>
  );
}

export { ForecastStory as default };
