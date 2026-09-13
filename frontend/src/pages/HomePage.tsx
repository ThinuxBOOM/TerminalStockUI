import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  getAuditForecasts,
  getHealth,
  getProvidersHealth,
  getQuote,
} from '../api/client';
import ProvenanceBadge from '../components/ProvenanceBadge';
import FreshnessBadge from '../components/FreshnessBadge';
import MarketStateBadge from '../components/MarketStateBadge';
import MarketLiquidityPanel from '../components/MarketLiquidityPanel';
import { useMarketLiquidity } from '../hooks/useMarketLiquidity';
import useWatchlist from '../hooks/useWatchlist';
import CurrencyValue from '../components/CurrencyValue';
import Skeleton from '../components/Skeleton';
import EmptyState from '../components/EmptyState';
import ErrorState, { StaleBanner } from '../components/ErrorState';

/** M8: one representative symbol per venue — market status is derived live
 *  from each quote's market_state + provenance, never hardcoded OPEN. */
const VENUES = [
  { mic: 'XNYS', label: 'NYSE (XNYS)', symbol: 'JPM' },
  { mic: 'XNAS', label: 'NASDAQ (XNAS)', symbol: 'AAPL' },
  { mic: 'XSHG', label: 'SSE (XSHG)', symbol: '600519.SS' },
  { mic: 'XPAR', label: 'Euronext Paris (XPAR)', symbol: 'MC.PA' },
  { mic: 'XAMS', label: 'Euronext Amsterdam (XAMS)', symbol: 'ASML.AS' },
  { mic: 'XBRU', label: 'Euronext Brussels (XBRU)', symbol: 'UCB.BR' },
] as const;

function normalizeSymbolInput(v: string): string {
  return v.trim().toUpperCase().replace(/\s+/g, '');
}

function VenueRow({ label, symbol }: { label: string; symbol: string }) {
  const q = useQuery({
    queryKey: ['quote', symbol],
    queryFn: () => getQuote(symbol),
    retry: false,
    staleTime: 30_000,
  });
  return (
    <li className="flex items-center justify-between gap-2 border-b border-term-border pb-1">
      <span className="min-w-0 truncate">{label}</span>
      {q.isLoading && (
        <span className="text-xs text-term-muted" role="status">
          …
        </span>
      )}
      {q.isError && (
        <span className="text-xs text-term-muted" role="status">
          unavailable
        </span>
      )}
      {q.data && (
        <span className="flex shrink-0 items-center gap-2">
          <MarketStateBadge state={q.data.market_state} provenance={q.data.provenance} />
          <span className="hidden text-[10px] text-term-muted lg:inline">
            {q.data.provenance.delay_minutes}m
          </span>
        </span>
      )}
    </li>
  );
}

function WatchlistRow({
  symbol,
  onRemove,
}: {
  symbol: string;
  onRemove: (s: string) => void;
}) {
  const q = useQuery({ queryKey: ['quote', symbol], queryFn: () => getQuote(symbol), retry: false });
  if (q.isLoading)
    return (
      <li className="p-3 text-xs text-term-muted" role="status">
        … {symbol}
      </li>
    );
  if (q.isError || !q.data)
    return (
      <li className="flex items-center justify-between gap-2 p-3 text-xs">
        <span className="min-w-0 truncate text-term-muted">{symbol} — unavailable</span>
        <span className="flex shrink-0 gap-2">
          <Link className="text-term-green" to={`/security/${encodeURIComponent(symbol)}`}>
            BRIEF →
          </Link>
          <button
            type="button"
            className="text-term-muted hover:text-term-red"
            onClick={() => onRemove(symbol)}
            aria-label={`Remove ${symbol} from watchlist`}
          >
            ✕
          </button>
        </span>
      </li>
    );
  const d = q.data;
  return (
    <li className="p-3">
      <div className="flex items-center justify-between gap-2 text-sm">
        <Link
          to={`/security/${encodeURIComponent(symbol)}`}
          className="min-w-0 truncate font-bold text-term-green hover:underline"
        >
          {d.symbol} ·{' '}
          <CurrencyValue value={d.price} currency={d.currency ?? 'USD'} />
        </Link>
        <span className="flex shrink-0 items-center gap-2">
          <FreshnessBadge p={d.provenance} />
          <button
            type="button"
            className="text-xs text-term-muted hover:text-term-red"
            onClick={() => onRemove(symbol)}
            aria-label={`Remove ${symbol} from watchlist`}
          >
            ✕
          </button>
        </span>
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-2">
        <MarketStateBadge state={d.market_state} provenance={d.provenance} />
        <ProvenanceBadge p={d.provenance} />
      </div>
    </li>
  );
}

export default function HomePage() {
  const health = useQuery({ queryKey: ['health'], queryFn: getHealth, retry: false });
  const providers = useQuery({
    queryKey: ['providers-health'],
    queryFn: getProvidersHealth,
    retry: false,
    staleTime: 30_000,
  });
  const research = useQuery({
    queryKey: ['audit-forecasts', 'recent'],
    queryFn: () => getAuditForecasts(5),
    retry: false,
    staleTime: 60_000,
  });
  const liquidity = useMarketLiquidity();

  const { symbols: watchlist, add: addWatchSymbol, remove: removeWatchSymbol } =
    useWatchlist();
  const [draft, setDraft] = useState('');

  function addSymbol() {
    const sym = normalizeSymbolInput(draft);
    if (!sym) return;
    addWatchSymbol(sym, 'manual');
    setDraft('');
  }

  function removeSymbol(sym: string) {
    removeWatchSymbol(sym);
  }

  const degraded = health.isError || health.data?.status !== 'ok';
  const providerRows = providers.data ?? null;
  const healthProviders = health.data?.providers ?? [];
  const showProviders = providerRows ?? healthProviders.map((p) => ({
    name: p.name,
    status: p.status,
    latency_ms: p.latency_ms,
    latency_p50_ms: p.latency_ms,
    latency_p95_ms: undefined,
    error_rate_1h: undefined,
    calls_1h: undefined,
    total_calls: undefined,
    circuit: undefined,
    last_check: p.last_check,
  }));

  const reports = research.data?.forecasts ?? [];

  return (
    <div className="grid max-w-full gap-4 md:grid-cols-3">
      <section className="term-panel min-w-0 p-4" aria-labelledby="home-market-status">
        <h2 id="home-market-status" className="term-label">
          Market status
        </h2>
        <ul className="mt-2 space-y-1 text-sm">
          {VENUES.map((v) => (
            <VenueRow key={v.mic} label={v.label} symbol={v.symbol} />
          ))}
        </ul>
        <p className="mt-2 text-[10px] text-term-muted">
          Live per-venue state from quote market_state + health — never hardcoded.
        </p>
      </section>

      <MarketLiquidityPanel
        data={liquidity.data ?? null}
        isLoading={liquidity.isLoading}
        isError={liquidity.isError}
        error={liquidity.error}
        onRetry={() => void liquidity.refetch()}
      />

      <section className="term-panel min-w-0 p-4" aria-labelledby="home-watchlist">
        <div className="flex items-center justify-between gap-2">
          <h2 id="home-watchlist" className="term-label">
            Watchlist
          </h2>
          <Link to="/watchlist" className="text-xs text-term-green">
            ALL →
          </Link>
        </div>
        <form
          className="mt-2 flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            addSymbol();
          }}
        >
          <input
            className="term-input min-w-0 flex-1"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Add symbol (e.g. MC.PA)"
            aria-label="Add symbol to watchlist"
            spellCheck={false}
          />
          <button className="term-btn shrink-0" type="submit">
            ADD
          </button>
        </form>
        {watchlist.length === 0 ? (
          <div className="mt-2">
            <EmptyState
              title="Watchlist is empty"
              detail="Add a symbol above — it persists in this browser."
            />
          </div>
        ) : (
          <ul className="mt-2 divide-y divide-term-border">
            {watchlist.map((s) => (
              <WatchlistRow key={s} symbol={s} onRemove={removeSymbol} />
            ))}
          </ul>
        )}
      </section>

      <div className="min-w-0 space-y-4">
        <section className="term-panel min-w-0 p-4" aria-labelledby="home-provider-health">
          <h2 id="home-provider-health" className="term-label">
            Provider health
          </h2>
          {(degraded || providers.isError) && (
            <div className="mt-2">
              <StaleBanner detail="health endpoint degraded — cached values shown" />
            </div>
          )}
          {providers.isLoading || health.isLoading ? (
            <div className="mt-2">
              <Skeleton label="loading provider health…" lines={3} />
            </div>
          ) : null}
          {showProviders.length > 0 ? (
            <ul className="mt-2 space-y-1 text-xs">
              {showProviders.map((p) => {
                const latency = p.latency_p50_ms ?? p.latency_ms ?? p.latency_p95_ms;
                const bad =
                  p.status !== 'ok' || (p.circuit !== undefined && p.circuit === 'open');
                return (
                  <li
                    key={p.name}
                    className="flex justify-between gap-2 border-b border-term-border pb-1"
                  >
                    <span className="min-w-0 truncate">{p.name}</span>
                    <span className={bad ? 'text-term-red' : 'text-term-green'}>
                      {p.status}
                      {p.circuit ? ` · ${p.circuit}` : ''}
                      {latency !== undefined ? ` · ${latency}ms` : ''}
                    </span>
                  </li>
                );
              })}
            </ul>
          ) : (
            !health.isLoading &&
            !providers.isLoading && (
              <p className="mt-2 text-xs text-term-muted">
                yfinance · AKShare · FX — no live data (backend offline?).
              </p>
            )
          )}
          {providers.isError && (
            <p className="mt-2 text-[11px] text-term-amber">
              ⚠ /api/providers/health unreachable
              {providers.error instanceof Error ? ` (${providers.error.message})` : ''} —
              showing /health summary.
            </p>
          )}
        </section>

        <section className="term-panel min-w-0 p-4" aria-labelledby="home-research">
          <h2 id="home-research" className="term-label">
            Latest research
          </h2>
          {research.isLoading && (
            <div className="mt-2">
              <Skeleton label="loading latest research…" lines={3} />
            </div>
          )}
          {research.isError && (
            <div className="mt-2">
              <ErrorState
                title="Research feed unavailable"
                detail={
                  research.error instanceof Error
                    ? research.error.message
                    : 'Backend /api/audit/forecasts unreachable.'
                }
                onRetry={() => void research.refetch()}
              />
            </div>
          )}
          {!research.isLoading && !research.isError && reports.length === 0 && (
            <p className="mt-1 text-xs text-term-muted">
              No reports yet. Scheduled reports land here (Milestone 4+).
            </p>
          )}
          {!research.isLoading && !research.isError && reports.length > 0 && (
            <ul className="mt-2 space-y-1 text-xs">
              {reports.map((r, i) => (
                <li
                  key={r.forecast_id ?? `${r.symbol}-${i}`}
                  className="flex items-center justify-between gap-2 border-b border-term-border pb-1"
                >
                  <Link
                    to={`/security/${encodeURIComponent(r.symbol ?? '')}`}
                    className="min-w-0 truncate font-bold text-term-green hover:underline"
                  >
                    {r.symbol ?? '—'}
                    {r.horizon_days ? ` · ${r.horizon_days}d` : ''}
                  </Link>
                  <span className="shrink-0 text-term-muted">
                    {typeof r.direction_probability === 'number'
                      ? `${(r.direction_probability * 100).toFixed(0)}%`
                      : '—'}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
