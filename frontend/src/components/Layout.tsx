import { Link, NavLink, useNavigate, useLocation } from 'react-router-dom';
import { useEffect, useRef, useState } from 'react';

const NAV = [
  { to: '/', label: 'HOME' },
  { to: '/search', label: 'SEARCH' },
  { to: '/watchlist', label: 'WATCHLIST' },
  { to: '/backtest', label: 'BACKTEST' },
  { to: '/providers', label: 'PROVIDERS' },
];

export default function Layout({ children }: { children: React.ReactNode }) {
  const [q, setQ] = useState('');
  const navigate = useNavigate();
  const location = useLocation();
  const isSecurity = location.pathname.startsWith('/security/');
  const searchRef = useRef<HTMLInputElement>(null);

  // M8 keyboard nav: "/" focuses global search from anywhere (outside
  // editable elements); Escape blurs it again.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const t = e.target as HTMLElement | null;
      const tag = (t?.tagName ?? '').toUpperCase();
      const editable =
        tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || t?.isContentEditable;
      if (e.key === '/' && !editable) {
        e.preventDefault();
        searchRef.current?.focus();
      } else if (e.key === 'Escape' && document.activeElement === searchRef.current) {
        searchRef.current?.blur();
      }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  return (
    <div className="min-h-screen max-w-full overflow-x-clip bg-term-bg">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-term-green focus:px-3 focus:py-1 focus:text-sm focus:text-black"
      >
        Skip to content
      </a>
      <header className="border-b border-term-border bg-term-panel">
        <div className="mx-auto flex max-w-7xl items-center gap-4 px-4 py-3">
          <Link to="/" className="shrink-0 text-term-green font-bold tracking-widest" aria-label="OneMarket home">
            ONE<span className="text-term-text">MARKET</span>
            <span className="ml-2 text-[10px] text-term-muted">TERMINAL v0.1</span>
          </Link>
          <form
            className="flex min-w-0 flex-1 gap-2"
            role="search"
            aria-label="Global search"
            onSubmit={(e) => {
              e.preventDefault();
              if (q.trim()) navigate(`/search?q=${encodeURIComponent(q.trim())}`);
            }}
          >
            <input
              ref={searchRef}
              className="term-input w-full min-w-0"
              placeholder="Search ticker / company  (e.g. AAPL, 600519.SS, ASML.AS)…  [ / ]"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              aria-label="Global search"
              spellCheck={false}
              autoComplete="off"
            />
            <button
              className="term-btn-ghost shrink-0"
              type="button"
              onClick={() => searchRef.current?.focus()}
              aria-label="Focus search (shortcut: slash key)"
              title="Focus search ( / )"
            >
              /
            </button>
          </form>
          <span
            className="hidden shrink-0 text-xs text-term-muted md:inline"
            role="status"
            aria-label={isSecurity ? 'Currently viewing a Security Brief' : 'Terminal'}
          >
            {isSecurity ? '● SECURITY BRIEF' : '○ TERMINAL'}
          </span>
        </div>
        <nav className="mx-auto flex max-w-7xl gap-1 overflow-x-auto px-4 pb-2" aria-label="Primary">
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              className={({ isActive }) =>
                `rounded px-3 py-1 text-xs tracking-widest ${
                  isActive
                    ? 'bg-term-border text-term-green'
                    : 'text-term-muted hover:text-term-text'
                }`
              }
            >
              {n.label}
            </NavLink>
          ))}
        </nav>
      </header>
      <main id="main-content" tabIndex={-1} className="mx-auto w-full max-w-7xl px-4 py-4">
        {children}
      </main>
      <footer
        role="contentinfo"
        className="mx-auto w-full max-w-7xl px-4 pb-6 text-[11px] text-term-muted"
      >
        Deterministic analytics are the source of truth. AI opinions are bounded and
        capped. Not investment advice.
      </footer>
    </div>
  );
}
