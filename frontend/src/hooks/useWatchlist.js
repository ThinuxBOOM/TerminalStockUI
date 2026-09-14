import { useCallback, useEffect, useState } from "react";
const WATCHLIST_KEY = "onemarket.watchlist.v1";
const WATCHLIST_SOURCES_KEY = "onemarket.watchlist.sources.v1";
const DEFAULT_WATCHLIST = ["AAPL", "MSFT", "600519.SS", "ASML.AS"];
const MAX_SYMBOLS = 30;
const SYMBOL_RE = /^[A-Z0-9][A-Z0-9.\-:]{0,31}$/;
function normalizeSymbol(v) {
  const upper = String(v ?? "").trim().toUpperCase().replace(/\s+/g, "");
  if (!upper) return "";
  // Allowlist the ticker alphabet (mirrors backend/security/validation.py):
  // strip anything outside [A-Z0-9.\-:] so localStorage content can never
  // smuggle markup/script into rendered output, then enforce shape.
  const clean = upper.replace(/[^A-Z0-9.\-:]/g, "").slice(0, 32);
  if (!clean || !SYMBOL_RE.test(clean) || clean.includes("..")) return "";
  return clean;
}
function dedupeCaseInsensitive(symbols) {
  const seen = /* @__PURE__ */ new Set();
  const out = [];
  for (const raw of symbols) {
    const sym = normalizeSymbol(raw);
    if (!sym) continue;
    const key = sym.toUpperCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(sym);
  }
  return out.slice(0, MAX_SYMBOLS);
}
function loadWatchlist() {
  try {
    if (typeof localStorage === "undefined") return [...DEFAULT_WATCHLIST];
    const raw = localStorage.getItem(WATCHLIST_KEY);
    if (!raw) return [...DEFAULT_WATCHLIST];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [...DEFAULT_WATCHLIST];
    const clean = dedupeCaseInsensitive(parsed.map((s) => String(s ?? "")));
    return clean.length > 0 ? clean : [...DEFAULT_WATCHLIST];
  } catch {
    return [...DEFAULT_WATCHLIST];
  }
}
function recordSource(symbol, source) {
  try {
    if (typeof localStorage === "undefined") return;
    const raw = localStorage.getItem(WATCHLIST_SOURCES_KEY);
    let map = {};
    if (raw) {
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        map = parsed;
      }
    }
    const key = normalizeSymbol(symbol);
    if (!key) return;
    map[key] = String(source);
    localStorage.setItem(WATCHLIST_SOURCES_KEY, JSON.stringify(map));
  } catch {
  }
}
function getWatchlistSources() {
  try {
    if (typeof localStorage === "undefined") return {};
    const raw = localStorage.getItem(WATCHLIST_SOURCES_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      const out = {};
      for (const [k, v] of Object.entries(parsed)) {
        const nk = normalizeSymbol(k);
        if (!nk) continue;
        out[nk] = String(v);
      }
      return out;
    }
    return {};
  } catch {
    return {};
  }
}
function useWatchlist() {
  const [symbols, setSymbols] = useState(loadWatchlist);
  const [lastRemoved, setLastRemoved] = useState(null);
  useEffect(() => {
    try {
      if (typeof localStorage !== "undefined") {
        localStorage.setItem(WATCHLIST_KEY, JSON.stringify(symbols));
      }
    } catch {
    }
  }, [symbols]);
  useEffect(() => {
    function onStorage(e) {
      if (e.key !== null && e.key !== WATCHLIST_KEY) return;
      try {
        if (e.newValue === null || e.newValue === void 0) {
          setSymbols([...DEFAULT_WATCHLIST]);
          return;
        }
        const parsed = JSON.parse(e.newValue);
        if (!Array.isArray(parsed)) return;
        const incoming = parsed.map((s) => String(s ?? ""));
        if (incoming.length === 0) {
          setSymbols([]);
          return;
        }
        const clean = dedupeCaseInsensitive(incoming);
        if (clean.length > 0) setSymbols(clean);
      } catch {
      }
    }
    if (typeof window !== "undefined") {
      window.addEventListener("storage", onStorage);
      return () => window.removeEventListener("storage", onStorage);
    }
    return void 0;
  }, []);
  const add = useCallback((symbol, source = "manual") => {
    const sym = normalizeSymbol(symbol);
    if (!sym) return;
    recordSource(sym, source);
    setSymbols((prev) => {
      if (prev.map((s) => s.toUpperCase()).includes(sym.toUpperCase())) return prev;
      return [...prev, sym].slice(0, MAX_SYMBOLS);
    });
  }, []);
  const remove = useCallback((symbol) => {
    const key = normalizeSymbol(symbol).toUpperCase();
    if (!key) return;
    setSymbols((prev) => {
      const found = prev.find((s) => s.toUpperCase() === key);
      if (found) setLastRemoved(found);
      return prev.filter((s) => s.toUpperCase() !== key);
    });
  }, []);
  const clear = useCallback(() => {
    setSymbols([]);
  }, []);
  const undoRemove = useCallback(() => {
    if (!lastRemoved) return;
    const sym = lastRemoved;
    setLastRemoved(null);
    setSymbols((prev) => {
      if (prev.map((s) => s.toUpperCase()).includes(sym.toUpperCase())) return prev;
      return [...prev, sym].slice(0, MAX_SYMBOLS);
    });
  }, [lastRemoved]);
  const exportJSON = useCallback(() => symbols, [symbols]);
  const importJSON = useCallback((arr) => {
    if (!Array.isArray(arr)) return;
    const clean = dedupeCaseInsensitive(arr.map((s) => String(s ?? "")));
    if (clean.length > 0) setSymbols(clean);
  }, []);
  return { symbols, add, remove, clear, lastRemoved, undoRemove, exportJSON, importJSON };
}
export { WATCHLIST_KEY, WATCHLIST_SOURCES_KEY, useWatchlist as default, getWatchlistSources };
