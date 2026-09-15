import { Link, NavLink, useNavigate, useLocation } from "react-router-dom";
import React, { memo, useCallback, useEffect, useRef, useState } from "react";
const NAV = [
  { to: "/", label: "HOME" },
  { to: "/welcome", label: "WELCOME" },
  { to: "/search", label: "SEARCH" },
  { to: "/screener", label: "SCREENER" },
  { to: "/watchlist", label: "WATCHLIST" },
  { to: "/backtest", label: "BACKTEST" },
  { to: "/providers", label: "PROVIDERS" },
  { to: "/login", label: "LOGIN (STUB)" }
];
const SearchBar = memo(function SearchBar({ q, setQ, onSubmit, searchRef }) {
  return /* @__PURE__ */ React.createElement(
    "form",
    {
      className: "order-3 flex w-full min-w-0 flex-1 gap-2 sm:order-none sm:w-auto",
      role: "search",
      "aria-label": "Global search",
      onSubmit
    },
    /* @__PURE__ */ React.createElement(
      "input",
      {
        ref: searchRef,
        className: "term-input w-full min-w-0",
        placeholder: "Search ticker / company  (e.g. AAPL, 600519.SS, ASML.AS)…  [ / or Ctrl+K ]",
        value: q,
        onChange: (e) => setQ(e.target.value),
        "aria-label": "Global search",
        "aria-keyshortcuts": "/ Control+k Meta+k",
        spellCheck: false,
        autoComplete: "off"
      }
    ),
    /* @__PURE__ */ React.createElement(
      "button",
      {
        className: "term-btn-ghost shrink-0",
        type: "button",
        onClick: () => searchRef.current?.focus(),
        "aria-label": "Focus search (shortcut: slash or Ctrl+K)",
        title: "Focus search ( / or Ctrl+K )"
      },
      "/"
    )
  );
});
function Layout({ children }) {
  const [q, setQ] = useState("");
  const navigate = useNavigate();
  const location = useLocation();
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
  return /* @__PURE__ */ React.createElement("div", { className: "min-h-screen max-w-full overflow-x-clip bg-term-bg" }, /* @__PURE__ */ React.createElement(
    "a",
    {
      href: "#main-content",
      className: "sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-term-green focus:px-3 focus:py-1 focus:text-sm focus:text-black"
    },
    "Skip to content"
  ), /* @__PURE__ */ React.createElement("header", { className: "border-b border-term-border bg-term-panel" }, /* @__PURE__ */ React.createElement("div", { className: "mx-auto flex max-w-7xl flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3" }, /* @__PURE__ */ React.createElement(Link, { to: "/", className: "shrink-0 text-term-green font-bold tracking-widest", "aria-label": "OneMarket home" }, "ONE", /* @__PURE__ */ React.createElement("span", { className: "text-term-text" }, "MARKET"), /* @__PURE__ */ React.createElement("span", { className: "ml-2 text-[10px] text-term-muted" }, "TERMINAL v0.1")), /* @__PURE__ */ React.createElement(SearchBar, { q, setQ, onSubmit, searchRef }), /* @__PURE__ */ React.createElement(
    "span",
    {
      className: "hidden shrink-0 text-xs text-term-muted md:inline",
      role: "status",
      "aria-label": isSecurity ? "Currently viewing a Security Brief" : "Terminal"
    },
    isSecurity ? "\u25CF SECURITY BRIEF" : "\u25CB TERMINAL"
  )), /* @__PURE__ */ React.createElement("nav", { className: "mx-auto flex max-w-7xl gap-1 overflow-x-auto whitespace-nowrap px-4 pb-2", "aria-label": "Primary" }, NAV.map((n) => /* @__PURE__ */ React.createElement(
    NavLink,
    {
      key: n.to,
      to: n.to,
      className: ({ isActive }) => `rounded px-3 py-1 text-xs tracking-widest ${isActive ? "bg-term-border text-term-green" : "text-term-muted hover:text-term-text"}`
    },
    n.label
  )))), /* @__PURE__ */ React.createElement("main", { id: "main-content", tabIndex: -1, className: "mx-auto w-full max-w-7xl px-4 py-4" }, children), /* @__PURE__ */ React.createElement(
    "footer",
    {
      role: "contentinfo",
      className: "mx-auto w-full max-w-7xl px-4 pb-6 text-[11px] text-term-muted"
    },
    "Deterministic analytics are the source of truth. AI opinions are bounded and capped. Not investment advice."
  ));
}
export { Layout as default };
