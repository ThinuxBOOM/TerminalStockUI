import React, { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import Sidebar from "./Sidebar";
import TopBar from "./TopBar";
import StatusBar from "./StatusBar";
import CommandPalette from "./CommandPalette";
import AdSlot from "../AdSlot";
import { useAuth } from "../../hooks/useAuth";

const SIDEBAR_KEY = "onemarket.sidebar.collapsed.v1";

function loadCollapsed() {
  try {
    if (typeof localStorage === "undefined") return false;
    return localStorage.getItem(SIDEBAR_KEY) === "1";
  } catch {
    return false;
  }
}

// Single terminal shell: <AppShell><Sidebar/><MainArea><TopBar/>
// <Workspace><Outlet/children/></Workspace><StatusBar/></MainArea></AppShell>.
// Owns global search navigation, command palette, focus management, and the
// compliant AdSlot budget. No page may duplicate shell markup.
function AppShell({ children }) {
  const [q, setQ] = useState("");
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(loadCollapsed);
  const navigate = useNavigate();
  const location = useLocation();
  const { tier } = useAuth();
  const searchRef = useRef(null);

  const toggleCollapse = useCallback(() => {
    setCollapsed((c) => {
      try {
        if (typeof localStorage !== "undefined") {
          localStorage.setItem(SIDEBAR_KEY, c ? "0" : "1");
        }
      } catch {
        // persistence must never break the shell
      }
      return !c;
    });
  }, []);

  const openPalette = useCallback(() => setPaletteOpen(true), []);
  const closePalette = useCallback(() => setPaletteOpen(false), []);

  // Global shortcuts: Ctrl/Cmd+K opens the palette (industry standard);
  // "/" focuses global search; Esc blurs it. The search input advertises
  // both shortcuts via aria-keyshortcuts (see TopBar).
  useEffect(() => {
    function onKey(e) {
      const t = e.target;
      const tag = (t?.tagName ?? "").toUpperCase();
      const editable = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || t?.isContentEditable;
      const modK = (e.ctrlKey || e.metaKey) && String(e.key ?? "").toLowerCase() === "k";
      if (modK) {
        e.preventDefault();
        setPaletteOpen((o) => !o);
      } else if (e.key === "/" && !editable && !paletteOpen) {
        e.preventDefault();
        searchRef.current?.focus();
      } else if (e.key === "Escape" && document.activeElement === searchRef.current) {
        searchRef.current?.blur();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [paletteOpen]);

  // Route change: move screen-reader/keyboard focus to main (skip-link
  // target) and close the mobile drawer. Mirrors legacy Layout behavior.
  useEffect(() => {
    document.getElementById("main-content")?.focus({ preventScroll: true });
    setMobileOpen(false);
  }, [location.pathname]);

  const onSubmit = useCallback(
    (e) => {
      e.preventDefault();
      if (q.trim()) navigate(`/search?q=${encodeURIComponent(q.trim())}`);
    },
    [q, navigate]
  );

  return (
    <div className="min-h-screen max-w-full overflow-x-clip bg-term-bg">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-term-green focus:px-3 focus:py-1 focus:text-sm focus:text-black"
      >
        Skip to content
      </a>
      <div className="flex min-h-screen items-stretch">
        <Sidebar
          collapsed={collapsed}
          mobileOpen={mobileOpen}
          onCloseMobile={() => setMobileOpen(false)}
          onToggleCollapse={toggleCollapse}
        />
        <div className="flex min-w-0 flex-1 flex-col">
          <TopBar
            q={q}
            setQ={setQ}
            onSubmit={onSubmit}
            searchRef={searchRef}
            onMenu={() => setMobileOpen(true)}
            menuExpanded={mobileOpen}
            onOpenPalette={openPalette}
          />
          {/* Compliant leaderboard below the header (slot index 0). */}
          <div className="w-full px-4 pt-3">
            <AdSlot slotId="layout-leaderboard" format="leaderboard" tier={tier} slotIndex={0} />
          </div>
          <main
            key={location.pathname}
            id="main-content"
            tabIndex={-1}
            className="route-fade w-full flex-1 px-4 py-4"
          >
            {children}
          </main>
          <footer
            role="contentinfo"
            className="w-full border-t border-term-border px-4 pb-2 pt-3 text-2xs text-term-muted"
          >
            {/* Footer in-feed above the disclaimer (slot index 1). */}
            <AdSlot slotId="layout-footer" format="in-feed" tier={tier} slotIndex={1} />
            Plain-English stock insights — no jargon needed. Numbers show their source and freshness. AI opinions are bounded and capped at 20%. Not investment advice.
          </footer>
          <StatusBar />
        </div>
      </div>
      <CommandPalette open={paletteOpen} onClose={closePalette} />
    </div>
  );
}

export { AppShell, AppShell as default };
