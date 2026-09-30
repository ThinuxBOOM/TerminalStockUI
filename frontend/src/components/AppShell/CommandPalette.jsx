import React, { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { GROUPS } from "./Sidebar";

const RECENT_KEY = "onemarket.recentSearches.v1";
const THEME_KEY = "onemarket.theme.v1";

const QUICK_LINKS = [
  { label: "Go to Markets", hint: "Top picks", to: "/screener" },
  { label: "Go to Watchlist", hint: "Stocks you follow", to: "/watchlist" },
  { label: "Go to Security Brief (AAPL)", hint: "Example brief", to: "/security/AAPL" },
];

// Phase 8: explicit page targets (stable even if Sidebar groups change).
const PAGES = [
  { label: "Overview", hint: "Terminal home", to: "/app" },
  { label: "Discover / Search", hint: "Search any company", to: "/search" },
  { label: "Screener", hint: "Best odds right now", to: "/screener" },
  { label: "Backtest Lab", hint: "How good were past calls?", to: "/backtest" },
  { label: "Watchlist", hint: "Stocks you follow", to: "/watchlist" },
  { label: "Data Health", hint: "Is the data fresh?", to: "/providers" },
  { label: "Security Brief (AAPL)", hint: "Example deep dive", to: "/security/AAPL" },
  { label: "Forecast (AAPL)", hint: "Example outlook", to: "/forecast/AAPL" },
  { label: "Account", hint: "Account and session", to: "/account" },
];

const SYMBOL_RE = /^\^?[A-Z0-9][A-Z0-9.\-:]{0,31}$/;
function asSymbol(v) {
  const sym = String(v ?? "").trim().toUpperCase().replace(/\s+/g, "");
  if (!sym || !SYMBOL_RE.test(sym) || sym.includes("..")) return "";
  return sym;
}

function toggleTheme() {
  // Phase 8 stub: terminal is dark-first (see index.css color-scheme: dark);
  // persist the preference + expose data-theme for future light tokens.
  // Best-effort only — never throws out of the palette.
  try {
    const cur = typeof localStorage !== "undefined" ? localStorage.getItem(THEME_KEY) : null;
    const next = cur === "light" ? "dark" : "light";
    try {
      if (typeof localStorage !== "undefined") localStorage.setItem(THEME_KEY, next);
    } catch {
      // persistence must never break the palette
    }
    try {
      if (typeof document !== "undefined" && document.documentElement) {
        document.documentElement.dataset.theme = next;
      }
    } catch {
      // DOM touch must never break the palette
    }
  } catch {
    // never throw out of an action
  }
}

function loadRecent() {
  try {
    if (typeof localStorage === "undefined") return [];
    const raw = localStorage.getItem(RECENT_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.map((s) => String(s ?? "").trim()).filter(Boolean).slice(0, 6);
  } catch {
    return [];
  }
}

// Isolated palette: local filter state only, no React Query, no writes to the
// production search / query state. Navigation targets are plain routes;
// actions run small sync callbacks (retry = reload, theme = persisted stub).
// Ctrl/⌘+K toggles (owned by AppShell), ↑↓/Enter/Esc navigate. No new deps.
function CommandPalette({ open, onClose }) {
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [index, setIndex] = useState(0);
  const inputRef = useRef(null);
  const listRef = useRef(null);

  const actions = useMemo(
    () => [
      {
        kind: "action",
        group: "ACTION",
        label: "Retry current view",
        hint: "Reload this page",
        run: () => {
          try {
            if (typeof window !== "undefined") window.location.reload();
          } catch {
            // reload must never throw out of the palette
          }
        },
      },
      {
        kind: "action",
        group: "ACTION",
        label: "Toggle theme",
        hint: "Dark-first stub (persists preference)",
        run: () => toggleTheme(),
      },
      {
        kind: "action",
        group: "ACTION",
        label: "Clear recent searches",
        hint: "Forget palette history",
        run: () => {
          try {
            if (typeof localStorage !== "undefined") localStorage.removeItem(RECENT_KEY);
          } catch {
            // clearing must never throw
          }
        },
      },
    ],
    []
  );

  useEffect(() => {
    if (open) {
      setQ("");
      setIndex(0);
      const t = setTimeout(() => inputRef.current?.focus(), 0);
      return () => clearTimeout(t);
    }
    return undefined;
  }, [open ]);

  const items = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const sym = asSymbol(q);
    // Symbol lookup ranks first when the input looks like a ticker:
    // Security Brief + Forecast + full-text search for that symbol.
    const symbolItems = sym
      ? [
          { kind: "symbol", group: "SYMBOL", label: `${sym} — Security Brief`, hint: "Deep dive", to: `/security/${encodeURIComponent(sym)}` },
          { kind: "symbol", group: "SYMBOL", label: `${sym} — Forecast`, hint: "Deterministic outlook", to: `/forecast/${encodeURIComponent(sym)}` },
          { kind: "symbol", group: "SYMBOL", label: `${sym} — Search`, hint: "Candidates + company match", to: `/search?q=${encodeURIComponent(sym)}` },
        ]
      : [];
    const nav = GROUPS.flatMap((g) =>
      g.items.map((it) => ({
        kind: "nav",
        group: g.title,
        label: it.label,
        hint: it.hint ?? it.to,
        to: it.to,
      }))
    );
    const pages = PAGES.map((p) => ({ kind: "page", group: "PAGE", ...p }));
    const recent = loadRecent().map((term) => ({
      kind: "recent",
      group: "RECENT",
      label: term,
      hint: "Recent search",
      to: `/search?q=${encodeURIComponent(term)}`,
    }));
    const quick = QUICK_LINKS.map((l) => ({ kind: "quick", group: "QUICK", ...l }));
    const all = [...symbolItems, ...recent, ...nav, ...pages, ...quick, ...actions];
    // De-dupe by destination (recents/symbols win) + by action label.
    const seen = new Set();
    const deduped = all.filter((it) => {
      const key = it.to ? `to:${it.to}` : `run:${it.label}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
    if (!needle && !sym) return deduped.slice(0, 12);
    // Symbol input: keep symbol targets pinned on top, filter the rest.
    const rest = deduped.slice(symbolItems.length).filter((it) =>
      `${it.label} ${it.hint} ${it.group} ${it.to ?? ""}`.toLowerCase().includes(needle)
    );
    return [...symbolItems, ...rest].slice(0, 12);
  }, [q, open, actions]);

  useEffect(() => {
    setIndex(0);
  }, [q]);

  function choose(it) {
    if (!it) return;
    try {
      if (typeof it.run === "function") it.run();
      else if (it.to) navigate(it.to);
    } catch {
      // navigation/actions must never throw out of the palette
    }
    if (onClose) onClose();
  }

  useEffect(() => {
    if (!open) return undefined;
    function onKey(e) {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose && onClose();
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        setIndex((i) => Math.min(items.length - 1, i + 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setIndex((i) => Math.max(0, i - 1));
      } else if (e.key === "Enter") {
        e.preventDefault();
        choose(items[index]);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, items, index, navigate, onClose]);

  useEffect(() => {
    // Keep the highlighted row visible (scroll only, no layout animation).
    try {
      listRef.current?.querySelector('[data-active="true"]')?.scrollIntoView({ block: "nearest" });
    } catch {
      // never break the palette on scroll failures
    }
  }, [index]);

  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 p-4 pt-20"
      onClick={() => onClose && onClose()}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        className="term-surface-overlay w-full max-w-lg overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="border-b border-term-border p-2">
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Type a symbol, company, market, or feature… (↑↓ Enter Esc)"
            aria-label="Command palette"
            aria-expanded="true"
            aria-controls="cmd-palette-list"
            aria-activedescendant={items[index] ? `cmd-item-${index}` : undefined}
            role="combobox"
            autoComplete="off"
            spellCheck={false}
            className="term-input w-full border-0 bg-transparent"
          />
        </div>
        <ul
          id="cmd-palette-list"
          ref={listRef}
          role="listbox"
          aria-label="Results"
          className="max-h-72 overflow-y-auto p-1"
        >
          {items.length === 0 ? (
            <li className="px-3 py-4 text-center text-xs text-term-muted" role="status">
              No matches — press Esc to close.
            </li>
          ) : (
            items.map((it, i) => (
              <li key={`${it.kind}:${it.to ?? it.label}`} id={`cmd-item-${i}`} role="option" aria-selected={i === index}>
                <button
                  type="button"
                  data-active={i === index ? "true" : "false"}
                  onMouseEnter={() => setIndex(i)}
                  onClick={() => choose(it)}
                  className={`flex w-full items-center justify-between gap-2 rounded px-3 py-2 text-left text-sm transition-colors duration-150 ${
                    i === index ? "bg-term-greenDim text-term-green" : "text-term-text"
                  }`.trim()}
                >
                  <span className="min-w-0 truncate">{it.label}</span>
                  <span className="shrink-0 text-2xs text-term-muted">
                    {it.group} · {it.hint}
                  </span>
                </button>
              </li>
            ))
          )}
        </ul>
      </div>
    </div>
  );
}

export { CommandPalette, CommandPalette as default };
