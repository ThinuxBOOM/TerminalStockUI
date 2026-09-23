import React, { useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import { ArrowRight } from "lucide-react";
import HeroTerminal from "./HeroTerminal.jsx";

/* Hero: hierarchy = WHAT (stock research terminal) -> WHY (data+forecasts+
 * research+signals+provenance) -> FEEL (fast, focused, dense) -> DO (Explore).
 * Background: fine grid + market lines + chart traces at extremely low
 * contrast. Scroll motion via rAF writing transform/opacity on refs directly
 * (no scroll setState spam). Fades gracefully under reduced motion. */
function HeroSection() {
  const textRef = useRef(null);
  const termRef = useRef(null);
  const bgRef = useRef(null);
  const sectionRef = useRef(null);

  useEffect(() => {
    const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) return undefined;
    let raf = 0;
    function update() {
      raf = 0;
      const el = sectionRef.current;
      if (!el) return;
      const r = el.getBoundingClientRect();
      const vh = window.innerHeight || 800;
      // progress: 0 while hero fills viewport, ->1 as it scrolls away
      const p = Math.min(1, Math.max(0, -r.top / (r.height || 1)));
      if (textRef.current) {
        textRef.current.style.transform = `translateY(${-p * 60}px)`;
        textRef.current.style.opacity = `${1 - p * 0.85}`;
      }
      if (termRef.current) {
        const s = 1 - p * 0.08;
        termRef.current.style.transform = `scale(${s}) translateY(${-p * 30}px)`;
        termRef.current.style.opacity = `${1 - p * 0.7}`;
      }
      if (bgRef.current) {
        bgRef.current.style.transform = `translateY(${p * 90}px)`;
        bgRef.current.style.opacity = `${1 - p * 0.9}`;
      }
    }
    function onScroll() {
      if (!raf) raf = requestAnimationFrame(update);
    }
    update();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      if (raf) cancelAnimationFrame(raf);
    };
  }, []);

  function scrollToDemo(e) {
    e.preventDefault();
    document.getElementById("demo")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  return (
    <section className="lp-hero" ref={sectionRef} aria-labelledby="landing-hero-h">
      <div className="lp-hero-bg" ref={bgRef} aria-hidden="true">
        <svg preserveAspectRatio="none" viewBox="0 0 1200 600">
          <defs>
            <pattern id="lp-grid" width="48" height="48" patternUnits="userSpaceOnUse">
              <path d="M48,0 L0,0 L0,48" fill="none" stroke="#1c2433" strokeWidth="1" opacity="0.35" />
            </pattern>
          </defs>
          <rect width="1200" height="600" fill="url(#lp-grid)" />
          <path d="M0,420 L150,400 L300,415 L450,370 L600,385 L750,340 L900,355 L1050,310 L1200,325" fill="none" stroke="#2a3448" strokeWidth="1.5" opacity="0.5" />
          <path d="M0,480 L150,470 L300,478 L450,450 L600,460 L750,430 L900,440 L1050,405 L1200,415" fill="none" stroke="#1c2433" strokeWidth="1.5" opacity="0.6" />
          <path d="M0,180 L200,170 L400,185 L600,150 L800,160 L1000,130 L1200,140" fill="none" stroke="#2a3448" strokeWidth="1" opacity="0.35" />
          <text x="60" y="120" fill="#2a3448" fontSize="22" fontFamily="monospace" opacity="0.6">6,482.31</text>
          <text x="880" y="220" fill="#2a3448" fontSize="22" fontFamily="monospace" opacity="0.6">21,384.9</text>
          <text x="420" y="540" fill="#2a3448" fontSize="22" fontFamily="monospace" opacity="0.6">19,204.55</text>
        </svg>
      </div>
      <div className="landing-inner lp-hero-grid">
        <div ref={textRef} style={{ willChange: "transform, opacity" }}>
          <p className="lp-kicker">The stock terminal for people who want to understand the market</p>
          <h1 id="landing-hero-h">
            Research stocks.<br />Understand forecasts.<br /><span className="accent">Follow the data.</span>
          </h1>
          <p className="lede">
            Market data, <b>forecasts</b>, research, signals and <b>provenance</b> in one fast,
            focused workspace — every number showing its source and age.
          </p>
          <div className="lp-hero-ctas">
            <Link to="/app" className="lp-btn">
              Open Terminal <ArrowRight aria-hidden="true" style={{ width: 15, height: 15 }} />
            </Link>
            <a href="#demo" onClick={scrollToDemo} className="lp-btn-ghost">
              Explore how it works
            </a>
          </div>
          <p className="lp-hero-note">Free to explore · No account · Demo data below — never fabricated live values.</p>
          <div className="lp-ticker" role="status" aria-label="Market snapshot (demo data)">
            <div className="lp-ticker-head"><span>MARKETS SNAPSHOT</span><span>DEMO DATA</span></div>
            <div className="lp-ticker-row lp-num">
              <span className="t"><span className="mut">S&amp;P 500</span> <span className="up">6,482.31 +0.4%</span></span>
              <span className="t"><span className="mut">NASDAQ</span> <span className="up">21,384.90 +0.7%</span></span>
              <span className="t"><span className="mut">ASPI</span> <span className="up">19,204.55 +0.2%</span></span>
            </div>
          </div>
        </div>
        <div ref={termRef} style={{ willChange: "transform, opacity" }}>
          <HeroTerminal />
        </div>
      </div>
    </section>
  );
}

export { HeroSection as default };
