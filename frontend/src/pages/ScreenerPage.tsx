import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import {
  FORECAST_HORIZONS,
  getScreener,
  type ForecastHorizon,
} from '../api/client';
import CurrencyValue from '../components/CurrencyValue';
import EmptyState from '../components/EmptyState';
import ErrorState, { StaleBanner } from '../components/ErrorState';
import MarketStateBadge from '../components/MarketStateBadge';
import ProvenanceBadge from '../components/ProvenanceBadge';
import Skeleton from '../components/Skeleton';

/** Market filter domain mirrors Search (All omits `?market=`). */
const MARKET_OPTIONS = [
  { label: 'All', value: '' },
  { label: 'NYSE', value: 'XNYS' },
  { label: 'NASDAQ', value: 'XNAS' },
  { label: 'SSE', value: 'XSHG' },
  { label: 'Euronext Paris', value: 'XPAR' },
  { label: 'Euronext Amsterdam', value: 'XAMS' },
  { label: 'Euronext Brussels', value: 'XBRU' },
] as const;

function clampProb(v: number): number {
  if (!Number.isFinite(v)) return 0.5;
  return Math.min(1, Math.max(0, v));
}

/** Backend `quality: {metric, quality_flag, reason}` — tolerant readers. */
function qualityInfo(r: { quality?: unknown }): { flag: string; reason: string } {
  const q = (r.quality ?? {}) as Record<string, unknown>;
  const flag = typeof q.quality_flag === 'string' && q.quality_flag ? q.quality_flag : '—';
  const reason = typeof q.reason === 'string' ? q.reason : '';
  return { flag, reason };
}

function qualityFlag(r: { quality?: unknown }): string {
  return qualityInfo(r).flag;
}

function qualityReason(r: { quality?: unknown }): string {
  return qualityInfo(r).reason || 'Quality signal unavailable for this row';
}

/** Timeout-aware error copy: a full live scan takes ~30s cold. */
const MAX_SKIPPED_SHOWN = 10;
function screenerErrorDetail(error: unknown): string {
  const message = error instanceof Error ? error.message : 'Backend unreachable. Check VITE_API_BASE_URL.';
  if (
    (error as { code?: unknown })?.code === 'ECONNABORTED' ||
    /timeout of \d+ms exceeded/i.test(message)
  ) {
    return 'Full-universe scan timed out (takes ~30s cold: 17 quotes + forecasts). Retry — warm quotes/cache make repeats faster.';
  }
  return message;
}

/**
 * Screener (Phase 3a): rank the registry universe by deterministic
 * forecast direction probability. Ranked rows link to the Security
 * Brief; fallback rows surface a StaleBanner instead of silent numbers.
 */
export default function ScreenerPage() {
  const [market, setMarket] = useState<string>('');
  const [horizon, setHorizon] = useState<ForecastHorizon>(21);
  const [minProb, setMinProb] = useState<number>(0.5);

  const mic = market.trim().toUpperCase();
  const screen = useQuery({
    queryKey: ['screener', mic || 'ALL', horizon, minProb],
    queryFn: () =>
      getScreener({
        market: mic || undefined,
        minDirection: minProb,
        horizon,
        limit: 20,
      }),
    staleTime: 30_000,
    retry: false,
  });

  const data = screen.data;
  const rows = useMemo(() => data?.results ?? [], [data]);
  const skippedCount = data?.skipped?.length ?? 0;
  const skippedSymbols = useMemo(
    () => (data?.skipped ?? []).slice(0, MAX_SKIPPED_SHOWN).map((s) => s.symbol),
    [data],
  );
  const skippedOverflow = skippedCount > skippedSymbols.length;
  const anyFallback = useMemo(
    () => rows.some((r) => r.provenance?.fallback_used === true),
    [rows],
  );

  return (
    <div>
      <h1 className="mb-3 text-sm tracking-widest text-term-muted">
        SCREENER · RANKED BY FORECAST DIRECTION
      </h1>

      <section className="term-panel p-4">
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label className="term-label" htmlFor="screener-market">
              Market
            </label>
            <select
              id="screener-market"
              className="term-input mt-1"
              value={market}
              onChange={(e) => setMarket(e.target.value)}
              aria-label="Filter screener by market"
            >
              {MARKET_OPTIONS.map((m) => (
                <option key={m.label} value={m.value}>
                  {m.label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <p className="term-label" id="screener-horizon-label">
              Horizon (trading days)
            </p>
            <div
              className="mt-1 flex gap-1"
              role="group"
              aria-labelledby="screener-horizon-label"
            >
              {FORECAST_HORIZONS.map((h) => (
                <button
                  key={h}
                  type="button"
                  className={h === horizon ? 'term-btn' : 'term-btn-ghost'}
                  aria-pressed={h === horizon}
                  onClick={() => setHorizon(h)}
                >
                  {h}d
                </button>
              ))}
            </div>
          </div>
          <div>
            <label className="term-label" htmlFor="screener-min-prob">
              Min probability {(minProb * 100).toFixed(0)}%
            </label>
            <div className="mt-1 flex items-center gap-2">
              <input
                id="screener-min-prob"
                type="range"
                min={0}
                max={1}
                step={0.01}
                value={minProb}
                onChange={(e) => setMinProb(clampProb(Number(e.target.value)))}
                aria-label="Minimum direction probability (slider)"
                className="w-40"
              />
              <input
                type="number"
                min={0}
                max={1}
                step={0.01}
                value={minProb}
                onChange={(e) => setMinProb(clampProb(Number(e.target.value)))}
                aria-label="Minimum direction probability (numeric)"
                className="term-input w-20"
              />
            </div>
          </div>
        </div>
        <p className="mt-2 text-[11px] text-term-muted">
          Deterministic ensemble forecast at {horizon}d · ranked by direction
          probability desc · seed universe only.
        </p>
      </section>

      <div className="mt-4">
        {screen.isLoading && (
          <Skeleton label="scanning universe…" lines={6} />
        )}
        {screen.isError && (
          <ErrorState
            title="Screener unavailable"
            detail={screenerErrorDetail(screen.error)}
            onRetry={() => void screen.refetch()}
          />
        )}
        {!screen.isLoading && !screen.isError && data && (
          <div className="space-y-3">
            {anyFallback && (
              <StaleBanner
                detail="screener rows include fallback data — prices/probabilities are stale-marked, not live"
              />
            )}
            <p className="text-xs text-term-muted" role="status">
              {data.count} of {data.universe_size} pass
              {skippedCount > 0 && ` · ${skippedCount} skipped (see below)`}
            </p>
            {rows.length === 0 ? (
              <EmptyState
                title="No instruments pass the screen"
                detail="Lower the minimum probability or widen the market filter."
                actionLabel="Reset filters"
                onAction={() => {
                  setMarket('');
                  setHorizon(21);
                  setMinProb(0.5);
                }}
              />
            ) : (
              <div className="term-panel overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-term-border text-left text-xs text-term-muted">
                      <th className="p-2">#</th>
                      <th className="p-2">Symbol</th>
                      <th className="p-2">Price</th>
                      <th className="p-2">Prob {horizon}d</th>
                      <th className="p-2">Confidence</th>
                      <th className="p-2">Quality</th>
                      <th className="p-2">Market</th>
                      <th className="p-2">Provenance</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r, idx) => (
                      <tr key={`${r.symbol}-${r.exchange_mic}-${idx}`} className="border-b border-term-border">
                        <td className="p-2 text-term-muted">{idx + 1}</td>
                        <td className="p-2">
                          <Link
                            to={`/security/${encodeURIComponent(r.symbol)}`}
                            className="font-bold text-term-green hover:underline"
                          >
                            {r.symbol}
                          </Link>
                          <span className="ml-2 text-xs text-term-muted">
                            {r.company_name}
                            {r.exchange_mic ? ` · ${r.exchange_mic}` : ''}
                          </span>
                        </td>
                        <td className="p-2">
                          <CurrencyValue value={r.price} currency={r.currency} />
                        </td>
                        <td className="p-2">
                          <b>
                            {Number.isFinite(r.direction_probability)
                              ? `${(r.direction_probability * 100).toFixed(1)}%`
                              : '—'}
                          </b>
                        </td>
                        <td className="p-2 text-xs">{r.confidence}</td>
                        <td
                          className="p-2 text-xs text-term-muted"
                          title={qualityReason(r)}
                        >
                          {qualityFlag(r)}
                        </td>
                        <td className="p-2">
                          <MarketStateBadge state={r.market_state} provenance={r.provenance} />
                        </td>
                        <td className="p-2">
                          <ProvenanceBadge p={r.provenance} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className="p-2 text-[11px] text-term-muted">
                  {data.disclosure || 'Not investment advice.'}
                  {skippedCount > 0 && (
                    <span className="ml-2">
                      Skipped: {skippedSymbols.join(', ')}{skippedOverflow ? ` +${skippedCount - skippedSymbols.length} more` : ''}.
                    </span>
                  )}
                </p>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
