import { Link, NavLink, useNavigate, useLocation } from "react-router-dom";
import React, { memo, useCallback, useEffect, useRef, useState } from "react";
import {
  LayoutDashboard,
  Sparkles,
  Search,
  SlidersHorizontal,
  Star,
  FlaskConical,
  Plug,
  LogIn,
  Crown,
} from "lucide-react";
import AdSlot from "./AdSlot";
import { useAuth } from "../hooks/useAuth";

const NAV = [
  { to: "/", label: "HOME", Icon: LayoutDashboard, hint: "Start here" },
  { to: "/search", label: "FIND STOCKS", Icon: Search, hint: "Search any company" },
  { to: "/screener", label: "TOP PICKS", Icon: SlidersHorizontal, hint: "Best odds right now" },
  { to: "/watchlist", label: "MY LIST", Icon: Star, hint: "Stocks you follow" },
  { to: "/backtest", label: "TEST IDEAS", Icon: FlaskConical, hint: "How good were past calls?" },
  { to: "/providers", label: "DATA HEALTH", Icon: Plug, hint: "Is the data fresh?" },
  { to: "/pricing", label: "PRICING", Icon: Crown, hint: "Plans and upgrades" },
  { to: "/welcome", label: "GUIDE", Icon: Sparkles, hint: "Learn in 2 minutes" },
  { to: "/login", label: "SIGN IN", Icon: LogIn, hint: "Sign in or create account" },
];

const SearchBar = memo(function SearchBar({ q, setQ, onSubmit, searchRef }) {
  return (
    <form
      className="order-3 flex w-full min-w-0 flex-1 gap-2 sm:order-none sm:w-auto"
      role="search"
      aria-label="Global search"
      onSubmit={onSubmit}
    >
      <div className="relative w-full min-w-0 flex-1">
        <Search
          className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-term-muted"
          aria-hidden="true"
        />
        <input
          ref={searchRef}
          className="term-input w-full min-w-0 pl-9 pr-12"
          placeholder="Search ticker / company  (e.g. AAPL, 600519.SS, ASML.AS)…  [ / or Ctrl+K ]"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          aria-label="Global search"
          aria-keyshortcuts="/ Control+k Meta+k"
          spellCheck={false}
          autoComplete="off"
        />
        <kbd
          aria-hidden="true"
          className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-2xs border border-term-border rounded px-1 text-term-muted"
        >
          ⌘K
        </kbd>
      </div>
    </form>
  );
});

function Layout({ children }) {
  const [q, setQ] = useState("");
  const navigate = useNavigate();
  const location = useLocation();
  // Tier mirror for compliant ads only (premium tiers don't mount slots).
  // Guest path resolves from the stub with zero requests.
  const { tier } = useAuth();
  const isSecurity = location.pathname.startsWith("/security/");
  const searchRef = useRef(null);
  useEffect(() => {
    function onKey(e) {
      const t = e.target;
      const tag = (t?.tagName ?? "").toUpperCase();
      const editable = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || t?.isContentEditable;
      const modK = (e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k";
      if (modK) {
        e.preventDefault();
        searchRef.current?.focus();
      } else if (e.key === "/" && !editable) {
        e.preventDefault();
        searchRef.current?.focus();
      } else if (e.key === "Escape" && document.activeElement === searchRef.current) {
        searchRef.current?.blur();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  // Move screen-reader/keyboard focus to the main region on route change
  // (the skip link target already carries tabIndex=-1).
  useEffect(() => {
    document.getElementById("main-content")?.focus({ preventScroll: true });
  }, [location.pathname]);
  const onSubmit = useCallback((e) => {
    e.preventDefault();
    if (q.trim()) navigate(`/search?q=${encodeURIComponent(q.trim())}`);
  }, [q, navigate]);
  return (
    <div className="min-h-screen max-w-full overflow-x-clip bg-term-bg">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-term-green focus:px-3 focus:py-1 focus:text-sm focus:text-black"
      >
        Skip to content
      </a>
      <header className="sticky top-0 z-40 border-b border-term-border bg-term-panel">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
          <Link to="/" className="flex shrink-0 items-center gap-2" aria-label="OneMarket home">
            <img src="/logo.svg" alt="OneMarket logo" className="h-8 w-8 rounded-lg" width="32" height="32" />
            <span className="font-sans font-black text-base tracking-tight text-term-green">
              ONE<span className="text-term-text">MARKET</span>
              <span className="ml-2 font-sans text-2xs font-normal tracking-normal text-term-muted/70">EASY INVESTING</span>
            </span>
          </Link>
          <SearchBar q={q} setQ={setQ} onSubmit={onSubmit} searchRef={searchRef} />
          <span
            className="hidden shrink-0 text-xs text-term-muted md:inline"
            role="status"
            aria-label={isSecurity ? "Currently viewing a Security Brief" : "Terminal"}
          >
            {isSecurity ? "● SECURITY BRIEF" : "○ TERMINAL"}
          </span>
        </div>
        <nav className="mx-auto flex max-w-7xl gap-1 overflow-x-auto whitespace-nowrap px-4 pb-2" aria-label="Primary">
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              title={n.hint ?? n.label}
              className={({ isActive }) =>
                `inline-flex items-center gap-1.5 rounded px-3 py-1 font-sans text-xs tracking-widest transition-colors duration-150 ${isActive ? "bg-term-greenDim text-term-green border-b-2 border-term-green" : "text-term-muted hover:text-term-text"}`
              }
            >
              <n.Icon className="h-3.5 w-3.5" aria-hidden="true" />
              {n.label}
            </NavLink>
          ))}
        </nav>
      </header>
      {/* V2 compliant ads: leaderboard below the header (slot index 0 of the
          per-tier budget). Never inside the sticky header. Suppressed tiers
          and VITE_ADS_ENABLED=false mount nothing (zero requests). */}
      <div className="mx-auto w-full max-w-7xl px-4 pt-3">
        <AdSlot slotId="layout-leaderboard" format="leaderboard" tier={tier} slotIndex={0} />
      </div>
      <main key={location.pathname} id="main-content" tabIndex={-1} className="route-fade mx-auto w-full max-w-7xl px-4 py-4">
        {children}
      </main>
      <footer
        role="contentinfo"
        className="mx-auto w-full max-w-7xl border-t border-term-border px-4 pb-6 pt-3 text-2xs text-term-muted"
      >
        {/* Footer in-feed above the disclaimer (slot index 1). */}
        <AdSlot slotId="layout-footer" format="in-feed" tier={tier} slotIndex={1} />
        Plain-English stock insights — no jargon needed. Numbers show their source and freshness. AI opinions are bounded and capped at 20%. Not investment advice.
      </footer>
    </div>
  );
}

export { Layout as default };
