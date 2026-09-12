import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';
import { displaySymbol, searchInstruments } from '../../api/client';
import Loading from '../../components/Loading';
import ErrorState, { StaleBanner } from '../../components/ErrorState';
import EmptyState from '../../components/EmptyState';

export const MARKET_OPTIONS = [
  { label: 'All', value: '' },
  { label: 'NYSE', value: 'XNYS' },
  { label: 'NASDAQ', value: 'XNAS' },
  { label: 'SSE', value: 'XSHG' },
  { label: 'Euronext Paris', value: 'XPAR' },
  { label: 'Euronext Amsterdam', value: 'XAMS' },
  { label: 'Euronext Brussels', value: 'XBRU' },
] as const;

const RECENT_KEY = 'onemarket.recentSearches.v1';
const RECENT_MAX = 6;
const DEBOUNCE_MS = 300;

function loadRecent(): string[] {
  try {
    const raw = localStorage.getItem(RECENT_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.map((s) => String(s ?? '').trim()).filter(Boolean).slice(0, RECENT_MAX);
  } catch {
    return [];
  }
}

function glyphFor(currency?: string): string {
  const c = (currency ?? '').trim().toUpperCase();
  if (c === 'USD') return '$';
  if (c === 'CNY') return '¥';
  if (c === 'EUR') return '€';
  return '';
}

export function useInstrumentSearch(query: string, market: string, enabled: boolean) {
  const mic = market.trim().toUpperCase();
  return useQuery({
    queryKey: ['instruments', 'search', query, mic || 'ALL'],
    queryFn: () => searchInstruments(query, mic || undefined),
    enabled,
  });
}

export default function SearchBox({
  initial = '',
  initialMarket = '',
}: {
  initial?: string;
  initialMarket?: string;
}) {
  const [q, setQ] = useState(initial);
  const [market, setMarket] = useState(initialMarket);
  const [debounced, setDebounced] = useState(initial);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [recent, setRecent] = useState<string[]>(loadRecent);
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);

  // M8: 300ms debounce so typing doesn't fan out one request per keystroke.
  useEffect(() => {
    const t = setTimeout(() => {
      setDebounced(q);
      setActiveIndex(-1);
    }, DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [q]);

  // Keep local state in sync when the route query changes (Layout search).
  useEffect(() => {
    setQ(initial);
    setDebounced(initial);
  }, [initial]);
  useEffect(() => {
    setMarket(initialMarket);
  }, [initialMarket]);

  const submitted = debounced.trim();
  const mic = market.trim().toUpperCase();
  const { data, isLoading, isFetching, isError, error, refetch } = useInstrumentSearch(
    submitted,
    mic,
    submitted.length >= 1,
  );

  function saveRecent(term: string) {
    const t = term.trim();
    if (!t) return;
    setRecent((prev) => {
      const next = [t, ...prev.filter((s) => s.toLowerCase() !== t.toLowerCase())].slice(
        0,
        RECENT_MAX,
      );
      try {
        localStorage.setItem(RECENT_KEY, JSON.stringify(next));
      } catch {
        /* storage unavailable — recents stay in-memory */
      }
      return next;
    });
  }

  function clearRecent() {
    setRecent([]);
    try {
      localStorage.removeItem(RECENT_KEY);
    } catch {
      /* ignore */
    }
  }

  function goToSymbol(sym: string) {
    saveRecent(q.trim() || sym);
    navigate(`/security/${encodeURIComponent(sym)}`);
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const term = q.trim();
    if (!term) return;
    // Flush the debounce immediately so Enter never waits 300ms.
    setDebounced(term);
    saveRecent(term);
    if (data && data.length > 0) {
      const target =
        activeIndex >= 0 && activeIndex < data.length ? data[activeIndex] : data[0];
      goToSymbol(displaySymbol(target));
    } else {
      void refetch();
    }
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'ArrowDown' && data && data.length > 0) {
      e.preventDefault();
      setActiveIndex((i) => (i + 1) % data.length);
    } else if (e.key === 'ArrowUp' && data && data.length > 0) {
      e.preventDefault();
      setActiveIndex((i) => (i <= 0 ? data.length - 1 : i - 1));
    } else if (e.key === 'Enter' && data && data.length > 0 && q.trim()) {
      // Enter → first (or arrow-highlighted) result.
      e.preventDefault();
      const target =
        activeIndex >= 0 && activeIndex < data.length ? data[activeIndex] : data[0];
      goToSymbol(displaySymbol(target));
    }
  }

  const expanded = submitted.length >= 1 && (data?.length ?? 0) > 0;

  return (
    <div className="max-w-full">
      <form onSubmit={handleSubmit} className="flex max-w-full gap-2" role="search">
        <input
          ref={inputRef}
          className="term-input min-w-0 flex-1"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="AAPL · Moutai · 600519.SS · ASML.AS …"
          aria-label="Search instruments"
          role="combobox"
          aria-expanded={expanded}
          aria-controls="search-listbox"
          aria-autocomplete="list"
          aria-activedescendant={activeIndex >= 0 ? `search-option-${activeIndex}` : undefined}
          spellCheck={false}
          autoComplete="off"
        />
        <select
          className="term-input shrink-0"
          value={market}
          onChange={(e) => setMarket(e.target.value)}
          aria-label="Filter by market"
        >
          {MARKET_OPTIONS.map((m) => (
            <option key={m.label} value={m.value}>
              {m.label}
            </option>
          ))}
        </select>
        <button className="term-btn shrink-0" type="submit">
          SEARCH
        </button>
      </form>

      {!submitted && recent.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
          <span className="text-term-muted">Recent:</span>
          {recent.map((r) => (
            <button
              key={r}
              type="button"
              className="term-btn-ghost px-2 py-1 text-xs"
              onClick={() => setQ(r)}
              aria-label={`Re-run recent search ${r}`}
            >
              {r}
            </button>
          ))}
          <button
            type="button"
            className="text-[11px] text-term-muted hover:text-term-text"
            onClick={clearRecent}
            aria-label="Clear recent searches"
          >
            clear
          </button>
        </div>
      )}

      <div className="mt-4 min-w-0">
        {!submitted && (
          <div className="term-panel p-6 text-sm text-term-muted">
            Type a ticker or company name. Search is exchange-aware (XNYS / XNAS /
            XSHG / XPAR / XAMS / XBRU). Try <b className="text-term-text">AAPL</b> or{' '}
            <b className="text-term-text">Moutai</b>.
          </div>
        )}
        {submitted && isLoading && <Loading label={`searching “${submitted}”…`} />}
        {submitted && data && isFetching && !isLoading && (
          <p className="mb-2 text-[11px] text-term-muted" role="status">
            refreshing…
          </p>
        )}
        {submitted && isError && data && data.length > 0 && (
          <div className="mb-2">
            <StaleBanner
              detail={`search refresh failed (${error instanceof Error ? error.message : 'backend unreachable'}) — showing cached results`}
            />
          </div>
        )}
        {submitted && isError && (!data || data.length === 0) && (
          <ErrorState
            title="Search unavailable"
            detail={error instanceof Error ? error.message : 'Backend unreachable. Check VITE_API_BASE_URL.'}
            onRetry={() => void refetch()}
          />
        )}
        {submitted && !isLoading && !isError && (data?.length ?? 0) === 0 && (
          <EmptyState
            title={`No instruments found for “${submitted}”`}
            detail="Check spelling or exchange suffix (.SS / .PA / .AS / .BR)."
          />
        )}
        {submitted && !isLoading && !isError && (data?.length ?? 0) > 1 && (
          <div
            className="mb-2 rounded border border-term-amber p-2 text-xs text-term-amber"
            role="status"
          >
            Ambiguous — {data?.length} candidates for “{submitted}”. Not auto-resolved;
            pick the exact symbol or narrow with the market filter.
          </div>
        )}
        {data && data.length > 0 && (
          <ul
            id="search-listbox"
            role="listbox"
            aria-label="Search results"
            className="term-panel divide-y divide-term-border"
          >
            {data.map((r, i) => {
              const sym = displaySymbol(r);
              const curr = (r.currency ?? '').toUpperCase();
              const active = i === activeIndex;
              return (
                <li
                  key={`${sym}-${r.exchange_mic ?? ''}`}
                  id={`search-option-${i}`}
                  role="option"
                  aria-selected={active}
                  className={`flex items-center justify-between gap-2 p-3 ${active ? 'bg-term-border' : ''}`}
                >
                  <div className="min-w-0">
                    <Link
                      to={`/security/${encodeURIComponent(sym)}`}
                      className="font-bold text-term-green hover:underline"
                      onClick={() => saveRecent(submitted)}
                    >
                      {sym}
                    </Link>
                    <span className="ml-2 text-xs text-term-muted">
                      {r.company_name ?? ''} {r.exchange_mic ? `· ${r.exchange_mic}` : ''}{' '}
                      {curr ? `· ${curr}${glyphFor(curr)}` : ''}
                    </span>
                  </div>
                  <Link
                    className="term-btn-ghost shrink-0 text-xs"
                    to={`/security/${encodeURIComponent(sym)}`}
                    onClick={() => saveRecent(submitted)}
                    aria-label={`Open Security Brief for ${sym}`}
                  >
                    BRIEF →
                  </Link>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
