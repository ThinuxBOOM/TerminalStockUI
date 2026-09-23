import React, { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Menu, X } from "lucide-react";
import { useScrollProgress } from "./useScrollProgress.js";

const LINKS = [
  { href: "#product", label: "Product" },
  { href: "#research", label: "Research" },
  { href: "#how", label: "Guide" },
  { href: "#markets", label: "Markets" },
  { href: "#pricing", label: "Pricing" },
  { href: "#faq", label: "FAQ" },
];

/* Floating/sticky nav: transparent at top, semi-opaque terminal surface +
 * blur + border after scrolling, 76px -> 58px. Thin scroll-progress bar on
 * top. Mobile hamburger with aria-expanded/controls, Escape to close. */
function LandingNav({ fromHref, fromLabel }) {
  const [scrolled, setScrolled] = useState(false);
  const [open, setOpen] = useState(false);
  const progress = useScrollProgress();
  const btnRef = useRef(null);

  useEffect(() => {
    function onScroll() {
      setScrolled(window.scrollY > 24);
    }
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    function onKey(e) {
      if (e.key === "Escape") {
        setOpen(false);
        btnRef.current?.focus();
      }
    }
    if (open) window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open ]);

  function scrollToDemo(e) {
    e.preventDefault();
    setOpen(false);
    document.getElementById("demo")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  return (
    <>
      <div className="lp-progress" aria-hidden="true">
        <span style={{ transform: `scaleX(${progress})` }} />
      </div>
      <header className={`lp-nav${scrolled ? " is-scrolled" : ""}`}>
        <div className="landing-inner lp-nav-row">
          <a href="#top" className="lp-brand" aria-label="OneMarket terminal — back to top">
            <img src="/logo.svg" alt="" aria-hidden="true" width="30" height="30" />
            <span>ONE<b>MARKET</b></span>
            <span className="lp-demo-tag">TERMINAL</span>
          </a>
          <nav className="lp-links" aria-label="Landing sections">
            {LINKS.map((l) => (
              <a key={l.href} href={l.href}>{l.label}</a>
            ))}
          </nav>
          <div className="lp-nav-cta">
            <Link to="/login" className="lp-btn-ghost lp-explore" style={{ padding: "0.55rem 1rem", fontSize: "0.78rem" }}>
              Sign in
            </Link>
            <Link to="/login?mode=register" className="lp-btn" style={{ padding: "0.55rem 1.1rem", fontSize: "0.78rem" }}>
              Sign up
            </Link>
            <button
              ref={btnRef}
              type="button"
              className="lp-hamburger"
              aria-expanded={open}
              aria-controls="landing-mobile-menu"
              aria-label={open ? "Close menu" : "Open menu"}
              onClick={() => setOpen((v) => !v)}
            >
              {open ? <X aria-hidden="true" style={{ width: 18, height: 18 }} /> : <Menu aria-hidden="true" style={{ width: 18, height: 18 }} />}
            </button>
          </div>
        </div>
      </header>
      <div id="landing-mobile-menu" className={`lp-mobile-menu landing-inner${open ? " open" : ""}`}>
        {open ? (
          <nav aria-label="Landing sections mobile">
            {LINKS.map((l) => (
              <a key={l.href} href={l.href} onClick={() => setOpen(false)}>{l.label}</a>
            ))}
            <a href="#demo" onClick={scrollToDemo}>Explore the demo</a>
            <Link to="/login" onClick={() => setOpen(false)}>Sign in</Link>
            <Link to="/login?mode=register" onClick={() => setOpen(false)}>Sign up</Link>
            {fromHref ? (
              <p className="lp-backlink" style={{ padding: "0.65rem 0.25rem" }}>
                You came from <code>{fromLabel}</code> — <Link to={fromHref}>jump back →</Link>
              </p>
            ) : null}
          </nav>
        ) : null}
      </div>
    </>
  );
}

export { LandingNav as default };
