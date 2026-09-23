import React, { memo } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { LogIn, Menu, Search } from "lucide-react";
import { getHealth } from "../../api/client";
import { useAuth } from "../../hooks/useAuth";
import StatusDot from "../ui/StatusDot";

// Reuses the SearchBar logic formerly in Layout.jsx: controlled `q`,
// submit -> /search?q=, "/" focuses, Esc blurs. Ctrl/Cmd+K is owned by the
// CommandPalette (AppShell opens the dialog); the input still advertises the
// shortcut for discoverability and focuses via "/" at all times.
const SearchBar = memo(function SearchBar({ q, setQ, onSubmit, searchRef }) {
  return (
    <form
      className="order-3 flex w-full min-w-0 flex-1 gap-2 sm:order-none sm:w-auto"
      role="search"
      aria-label="Global search"
      onSubmit={onSubmit}
    >
      <div className="relative w-full min-w-0 flex-1 sm:max-w-md">
        <Search
          className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-term-muted"
          aria-hidden="true"
        />
        <input
          ref={searchRef}
          id="global-search"
          className="term-input w-full min-w-0 truncate"
          style={{ paddingLeft: "2.25rem", paddingRight: "3rem" }}
          placeholder="Search ticker or company…"
          title="Search ticker / company (e.g. AAPL, 600519.SS, ASML.AS)"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          aria-label="Global search"
          aria-keyshortcuts="/ Control+k Meta+k"
          spellCheck={false}
          autoComplete="off"
        />
        <kbd
          aria-hidden="true"
          className="pointer-events-none absolute right-3 top-1/2 hidden -translate-y-1/2 rounded border border-term-border px-1 text-2xs text-term-muted sm:block"
        >
          ⌘K
        </kbd>
      </div>
    </form>
  );
});

function ConnectionDot() {
  // Shared ["health"] cache with StatusBar (staleTime 60s) — no extra request.
  const health = useQuery({
    queryKey: ["health"],
    queryFn: ({ signal }) => getHealth({ signal }),
    retry: false,
    staleTime: 60_000,
  });
  if (health.isError) return <StatusDot state="error" label="OFFLINE" />;
  if (!health.data) return <StatusDot state="neutral" label="···" />;
  return <StatusDot state="fresh" label="API" />;
}

function TopBar({ q, setQ, onSubmit, searchRef, onMenu, menuExpanded = false, onOpenPalette }) {
  const { isAuthenticated, loading } = useAuth();
  return (
    <header className="sticky top-0 z-40 border-b border-term-border bg-term-panel">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-2.5">
        <button
          type="button"
          onClick={onMenu}
          aria-label="Open navigation"
          aria-expanded={menuExpanded}
          aria-controls="app-sidebar-mobile"
          className="term-btn-sm shrink-0 lg:hidden"
        >
          <Menu className="h-4 w-4" aria-hidden="true" />
        </button>
        <Link to="/app" className="flex shrink-0 items-center gap-2" aria-label="OneMarket home">
          <img src="/logo.svg" alt="OneMarket logo" className="h-8 w-8 rounded-lg" width="32" height="32" />
          <span className="font-sans text-base font-black tracking-tight text-term-green">
            ONE<span className="text-term-text">MARKET</span>
            <span className="ml-2 font-sans text-2xs font-normal tracking-normal text-term-muted/70">EASY INVESTING</span>
          </span>
        </Link>
        <SearchBar q={q} setQ={setQ} onSubmit={onSubmit} searchRef={searchRef} />
        <div className="ml-auto flex shrink-0 items-center gap-2">
          <button
            type="button"
            onClick={onOpenPalette}
            className="term-btn-sm hidden md:inline-flex"
            aria-label="Open command palette (Control K)"
            title="Command palette (Ctrl/⌘+K)"
          >
            ⌘K PALETTE
          </button>
          <ConnectionDot />
          {loading ? (
            <span className="text-xs text-term-muted" role="status">···</span>
          ) : isAuthenticated ? (
            <Link to="/account" className="term-btn-sm" aria-label="Account">
              ● ACCOUNT
            </Link>
          ) : (
            <Link to="/login" className="term-btn-sm" aria-label="Sign in">
              <span className="inline-flex items-center gap-1">
                <LogIn className="h-3.5 w-3.5" aria-hidden="true" />
                SIGN IN
              </span>
            </Link>
          )}
        </div>
      </div>
    </header>
  );
}

export { SearchBar, TopBar, TopBar as default };
