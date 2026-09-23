import React, { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { GROUPS } from "./Sidebar";

const RECENT_KEY = "onemarket.recentSearches.v1";

const QUICK_LINKS = [
  { label: "Go to Markets", hint: "Top picks", to: "/screener" },
  { label: "Go to Watchlist", hint: "Stocks you follow", to: "/watchlist" },
  { label: "Go to Security Brief (AAPL)", hint: "Example brief", to: "/security/AAPL" },
];

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
// production search / query state. Navigation targets are plain routes.
function CommandPalette({ open, onClose }) {
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [index, setIndex] = useState(0);
  const inputRef = useRef(null);
  const listRef = useRef(null);

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
    const nav = GROUPS.flatMap((g) =>
      g.items.map((it) => ({
        kind: "nav",
        group: g.title,
        label: it.label,
        hint: it.hint ?? it.to,
        to: it.to,
      }))
    );
    const recent = loadRecent().map((term) => ({
      kind: "recent",
      group: "RECENT",
      label: term,
      hint: "Recent search",
      to: `/search?q=${encodeURIComponent(term)}`,
    }));
    const quick = QUICK_LINKS.map((l) => ({ kind: "quick", group: "QUICK", ...l }));
    const all = [...recent, ...nav, ...quick];
    // De-dupe by destination, keep first occurrence (recents win).
    const seen = new Set();
    const deduped = all.filter((it) => {
      if (seen.has(it.to)) return false;
      seen.add(it.to);
      return true;
    });
    if (!needle) return deduped.slice(0, 12);
    return deduped
      .filter((it) =>
        `${it.label} ${it.hint} ${it.group} ${it.to}`.toLowerCase().includes(needle)
      )
      .slice(0, 12);
  }, [q, open]);

  useEffect(() => {
    setIndex(0);
  }, [q]);

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
        const it = items[index];
        if (it) {
          navigate(it.to);
          onClose && onClose();
        }
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
              <li key={`${it.kind}:${it.to}`} id={`cmd-item-${i}`} role="option" aria-selected={i === index}>
                <button
                  type="button"
                  data-active={i === index ? "true" : "false"}
                  onMouseEnter={() => setIndex(i)}
                  onClick={() => {
                    navigate(it.to);
                    onClose && onClose();
                  }}
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
