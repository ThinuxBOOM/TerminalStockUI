import React, { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import Reveal from "./Reveal.jsx";
import { useCountUp } from "./useCountUp.js";

/* ForecastStory: forecasts that grade themselves. The demo panel leads with
 * the 21-day range (count-up once on enter), then volatility, drop risk and
 * the measured record of the direction lean. Never implies a guarantee. */
function ForecastStory() {
  const ref = useRef(null);
  const [entered, setEntered] = useState(false);
  const value = useCountUp(10, entered, 1100);

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
    { k: "VOLATILITY", v: "NORMAL" },
    { k: "10%+ DROP RISK", v: "16%" },
    { k: "DATA QUALITY", v: "A" },
  ];

  return (
    <section className="lp-section" aria-labelledby="forecast-h">
      <div className="landing-inner lp-split">
        <Reveal>
          <p className="lp-kicker">Forecasts</p>
          <h2 className="lp-h2" id="forecast-h">Forecasts that show their own report card.</h2>
          <p className="lp-sub">
            Each forecast leads with how far a stock has typically moved over the horizon, its
            volatility and its risk of a large drop. The up/down lean comes second, next to its
            measured record: in walk-forward tests it has not beaten a simple baseline, and the
            app tells you so instead of hiding it.
          </p>
          <p style={{ marginTop: 12 }}>
            <Link to="/forecast/AAPL" className="lp-btn-ghost" style={{ fontSize: "0.8rem" }}>See a full forecast →</Link>
          </p>
        </Reveal>
        <div ref={ref} className="lp-panel" style={{ padding: "1.6rem" }} aria-label="Forecast example: 21-day range minus 7 to plus 10 percent (demo data)">
          <p className="lp-label">21D range · 80% of past periods · Demo data</p>
          <p className="lp-big-num" aria-live="polite"><span className="lp-num">&minus;{Math.round(value * 0.7)}% to +{value}</span>%</p>
          <div className="lp-meta-row">
            {steps.map((s, i) => (
              <div key={s.k} className={`lp-meta${entered ? " is-on" : ""}`} style={{ transitionDelay: `${500 + i * 220}ms` }}>
                <p className="lp-label" style={{ fontSize: "0.6rem" }}>{s.k}</p>
                <p className="lp-num" style={{ fontWeight: 800, margin: "2px 0 0" }}>{s.v}</p>
              </div>
            ))}
          </div>
          <p style={{ fontSize: "0.85rem", lineHeight: 1.6, margin: "14px 0 0", opacity: entered ? 1 : 0, transition: "opacity 400ms ease 1150ms" }}>
            Direction lean 58% up · measured: no better than the base rate. Use the range to size risk, not to pick a side.
          </p>
        </div>
      </div>
    </section>
  );
}

export { ForecastStory as default };
