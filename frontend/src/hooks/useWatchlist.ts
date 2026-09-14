import { useCallback, useEffect, useState } from 'react';

export const WATCHLIST_KEY = 'onemarket.watchlist.v1';
export const WATCHLIST_SOURCES_KEY = 'onemarket.watchlist.sources.v1';

const DEFAULT_WATCHLIST = ['AAPL', 'MSFT', '600519.SS', 'ASML.AS'];
const MAX_SYMBOLS = 30;

export type WatchlistSource = 'manual' | 'backtest' | 'screener' | string;

function normalizeSymbol(v: unknown): string {
  return String(v ?? '').trim().toUpperCase().replace(/\s+/g, '');
}

function dedupeCaseInsensitive(symbols: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
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

function loadWatchlist(): string[] {
  try {
    if (typeof localStorage === 'undefined') return [...DEFAULT_WATCHLIST];
    const raw = localStorage.getItem(WATCHLIST_KEY);
    if (!raw) return [...DEFAULT_WATCHLIST];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [...DEFAULT_WATCHLIST];
    const clean = dedupeCaseInsensitive(parsed.map((s) => String(s ?? '')));
    return clean.length > 0 ? clean : [...DEFAULT_WATCHLIST];
  } catch {
    return [...DEFAULT_WATCHLIST];
  }
}

function recordSource(symbol: string, source: WatchlistSource): void {
  try {
    if (typeof localStorage === 'undefined') return;
    const raw = localStorage.getItem(WATCHLIST_SOURCES_KEY);
    let map: Record<string, string> = {};
    if (raw) {
      const parsed: unknown = JSON.parse(raw);
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        map = parsed as Record<string, string>;
      }
    }
    // Case-insensitive key (strip spaces, UPPER) so `aapl` and `AAPL`
    // share one audit entry instead of forking.
    const key = normalizeSymbol(symbol);
    if (!key) return;
    map[key] = String(source);
    localStorage.setItem(WATCHLIST_SOURCES_KEY, JSON.stringify(map));
  } catch {
    /* storage unavailable — audit map stays in-memory */
  }
}

export function getWatchlistSources(): Record<string, string> {
  try {
    if (typeof localStorage === 'undefined') return {};
    const raw = localStorage.getItem(WATCHLIST_SOURCES_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      // Normalize legacy keys case-insensitively; last write wins on
      // collision. Valid (already UPPER, spaceless) maps pass through
      // unchanged.
      const out: Record<string, string> = {};
      for (const [k, v] of Object.entries(parsed as Record<string, unknown>)) {
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

/**
 * Unified watchlist hook backed by localStorage key `onemarket.watchlist.v1`.
 * Same key/defaults as HomePage (`[AAPL,MSFT,600519.SS,ASML.AS]`), so the
 * Home watchlist and the Watchlist page share one persisted list.
 * Dedupe is case-insensitive, capped at 30 symbols, with a cross-tab
 * `storage` listener. `add(symbol, source?)` records the source into
 * `onemarket.watchlist.sources.v1` for audit (manual|backtest|screener).
 */
export default function useWatchlist() {
  const [symbols, setSymbols] = useState<string[]>(loadWatchlist);

  // Persist on every change.
  useEffect(() => {
    try {
      if (typeof localStorage !== 'undefined') {
        localStorage.setItem(WATCHLIST_KEY, JSON.stringify(symbols));
      }
    } catch {
      /* storage unavailable — watchlist stays in-memory */
    }
  }, [symbols]);

  // Cross-tab sync: another tab's write updates this tab's state.
  // Handles clear() (key null), removeItem (newValue null), and explicit
  // writes. An explicit empty array (clear()) stays empty; a missing key
  // (fresh/removed storage) resets to the shared defaults.
  useEffect(() => {
    function onStorage(e: StorageEvent) {
      if (e.key !== null && e.key !== WATCHLIST_KEY) return;
      try {
        if (e.newValue === null || e.newValue === undefined) {
          setSymbols([...DEFAULT_WATCHLIST]);
          return;
        }
        const parsed: unknown = JSON.parse(e.newValue);
        if (!Array.isArray(parsed)) return;
        const incoming = parsed.map((s) => String(s ?? ''));
        // Preserve an explicit clear ([] stays []); only fall back to
        // defaults when the payload carries no valid symbols but is not
        // an explicit empty write.
        if (incoming.length === 0) {
          setSymbols([]);
          return;
        }
        const clean = dedupeCaseInsensitive(incoming);
        if (clean.length > 0) setSymbols(clean);
      } catch {
        /* ignore malformed cross-tab payloads */
      }
    }
    if (typeof window !== 'undefined') {
      window.addEventListener('storage', onStorage);
      return () => window.removeEventListener('storage', onStorage);
    }
    return undefined;
  }, []);

  const add = useCallback((symbol: string, source: WatchlistSource = 'manual') => {
    const sym = normalizeSymbol(symbol);
    if (!sym) return;
    recordSource(sym, source);
    setSymbols((prev) => {
      if (prev.map((s) => s.toUpperCase()).includes(sym.toUpperCase())) return prev;
      return [...prev, sym].slice(0, MAX_SYMBOLS);
    });
  }, []);

  const remove = useCallback((symbol: string) => {
    const key = normalizeSymbol(symbol).toUpperCase();
    if (!key) return;
    setSymbols((prev) => prev.filter((s) => s.toUpperCase() !== key));
  }, []);

  const clear = useCallback(() => {
    setSymbols([]);
  }, []);

  return { symbols, add, remove, clear };
}
