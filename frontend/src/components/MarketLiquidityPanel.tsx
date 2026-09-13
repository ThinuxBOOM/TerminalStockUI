import EmptyState from './EmptyState';
import ErrorState from './ErrorState';
import FreshnessBadge from './FreshnessBadge';
import ProvenanceBadge from './ProvenanceBadge';
import Skeleton from './Skeleton';
import {
  isStaleLiquidity,
  type MarketBreadth,
  type MarketsOverview,
} from '../api/markets';

export type MarketLiquidityPanelProps = {
  data?: MarketsOverview | null;
  isLoading?: boolean;
  isError?: boolean;
  error?: unknown;
  onRetry?: () => void;
};

const compactFmt = new Intl.NumberFormat('en', {
  notation: 'compact',
  maximumFractionDigits: 1,
});

function formatCompact(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return 'unavailable';
  try {
    return compactFmt.format(v);
  } catch {
    return String(v);
  }
}

function formatSignedPct(v: number | null | undefined): { text: string; tone: string } {
  if (v === null || v === undefined || !Number.isFinite(v)) {
    return { text: 'unavailable', tone: 'text-term-muted' };
  }
  const sign = v > 0 ? '+' : '';
  return {
    text: `${sign}${v.toFixed(2)}%`,
    tone: v > 0 ? 'text-term-green' : v < 0 ? 'text-term-red' : 'text-term-muted',
  };
}

function formatPlainPct(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return 'unavailable';
  return `${v.toFixed(2)}%`;
}

function errorMessage(err: unknown): string {
  if (err instanceof Error && err.message) return err.message;
  const e = err as { response?: { data?: unknown }; message?: unknown } | null;
  const data = e?.response?.data as Record<string, unknown> | string | undefined;
  if (typeof data === 'string' && data) return data;
  if (data && typeof data === 'object') {
    const detail = (data.detail ?? data.message) as unknown;
    if (typeof detail === 'string' && detail) return detail;
  }
  if (typeof e?.message === 'string' && e.message) return e.message;
  return 'Backend /api/markets/overview unreachable and screener fallback failed.';
}

function MarketCard({ m }: { m: MarketBreadth }) {
  const total = m.total > 0 ? m.total : m.advancers + m.decliners + m.unchanged;
  const advW = total > 0 ? (m.advancers / total) * 100 : 0;
  const decW = total > 0 ? (m.decliners / total) * 100 : 0;
  const unchW = total > 0 ? Math.max(0, 100 - advW - decW) : 0;
  const stale = isStaleLiquidity(m.provenance);
  const avg = formatSignedPct(m.avg_change_pct);
  const stateEntries = Object.entries(m.market_state_counts);

  return (
    <article
      className="min-w-0 rounded border border-term-border bg-term-bg p-3"
      aria-label={`${m.label || m.mic} liquidity and breadth`}
    >
      <div className="flex items-center justify-between gap-2">
        <h3 className="min-w-0 truncate text-sm font-bold text-term-text">
          {m.label || m.mic}
        </h3>
        <span className="shrink-0 text-[10px] text-term-muted">n={total}</span>
      </div>

      {/* Advancers / decliners / unchanged stacked bar */}
      <div
        className="mt-2 flex h-2 w-full overflow-hidden rounded bg-term-border"
        role="img"
        aria-label={`${m.mic}: ${m.advancers} advancers, ${m.decliners} decliners, ${m.unchanged} unchanged`}
        title={`adv ${m.advancers} / dec ${m.decliners} / unch ${m.unchanged}`}
      >
        <div className="h-full bg-term-green" style={{ width: `${advW}%` }} />
        <div className="h-full bg-term-red" style={{ width: `${decW}%` }} />
        <div className="h-full bg-term-muted" style={{ width: `${unchW}%` }} />
      </div>
      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px]">
        <span className="text-term-green">▲ {m.advancers}</span>
        <span className="text-term-red">▼ {m.decliners}</span>
        <span className="text-term-muted">■ {m.unchanged}</span>
      </div>

      <dl className="mt-2 space-y-1 text-xs">
        <div className="flex items-center justify-between gap-2">
          <dt className="text-term-muted">Avg change</dt>
          <dd className={`font-bold ${avg.tone}`}>{avg.text}</dd>
        </div>
        <div className="flex items-center justify-between gap-2">
          <dt className="text-term-muted">Volume</dt>
          <dd className="text-term-text">{formatCompact(m.total_volume)}</dd>
        </div>
        <div className="flex items-center justify-between gap-2">
          <dt className="text-term-muted">Turnover</dt>
          <dd className="text-term-text">{formatCompact(m.turnover)}</dd>
        </div>
        <div className="flex items-center justify-between gap-2">
          <dt className="text-term-muted">Avg range</dt>
          <dd className="text-term-text">{formatPlainPct(m.avg_range_pct)}</dd>
        </div>
      </dl>

      <div className="mt-2 flex flex-wrap gap-1" aria-label={`${m.mic} market states`}>
        {stateEntries.length === 0 && (
          <span className="text-[10px] text-term-muted">no state data</span>
        )}
        {stateEntries.map(([state, count]) => (
          <span
            key={state}
            className="rounded border border-term-border px-1.5 py-0.5 text-[10px] text-term-muted"
            title={`${count} instrument(s) with market_state=${state}`}
          >
            {state}×{count}
          </span>
        ))}
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <FreshnessBadge p={m.provenance} />
        <ProvenanceBadge p={m.provenance} />
      </div>
      {stale && (
        <p className="mt-1.5 text-[10px] text-term-amber" role="note">
          Stale/fallback figures — shown for context, never ranked.
        </p>
      )}
    </article>
  );
}

/**
 * Homepage per-market liquidity + breadth section (XNYS/XNAS/XSHG/XPAR/XAMS/XBRU).
 * All figures come from props (hook data) — no prices are hardcoded.
 * Stale data (fallback_used or grade D) is gated with an honest badge and
 * markets are never ranked without fresh provenance.
 */
export default function MarketLiquidityPanel({
  data,
  isLoading,
  isError,
  error,
  onRetry,
}: MarketLiquidityPanelProps) {
  if (isLoading) {
    return (
      <section
        className="term-panel min-w-0 p-4 md:col-span-3 md:row-start-2"
        aria-labelledby="home-liquidity"
      >
        <h2 id="home-liquidity" className="term-label">
          Market liquidity &amp; breadth
        </h2>
        <div className="mt-2">
          <Skeleton label="loading market liquidity…" lines={6} />
        </div>
      </section>
    );
  }

  if (isError) {
    return (
      <section
        className="term-panel min-w-0 p-4 md:col-span-3 md:row-start-2"
        aria-labelledby="home-liquidity"
      >
        <h2 id="home-liquidity" className="term-label">
          Market liquidity &amp; breadth
        </h2>
        <div className="mt-2">
          <ErrorState
            title="Market liquidity unavailable"
            detail={errorMessage(error)}
            onRetry={onRetry}
          />
        </div>
      </section>
    );
  }

  const markets = data?.markets ?? [];
  if (markets.length === 0) {
    return (
      <section
        className="term-panel min-w-0 p-4 md:col-span-3 md:row-start-2"
        aria-labelledby="home-liquidity"
      >
        <h2 id="home-liquidity" className="term-label">
          Market liquidity &amp; breadth
        </h2>
        <div className="mt-2">
          <EmptyState
            title="No market breadth yet"
            detail="The screener returned no rows for XNYS / XNAS / XSHG / XPAR / XAMS / XBRU."
            actionLabel={onRetry ? 'Retry' : undefined}
            onAction={onRetry}
          />
        </div>
      </section>
    );
  }

  return (
    <section
      className="term-panel min-w-0 p-4 md:col-span-3 md:row-start-2"
      aria-labelledby="home-liquidity"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="home-liquidity" className="term-label">
          Market liquidity &amp; breadth
        </h2>
        {data && <FreshnessBadge p={data.provenance} />}
      </div>
      {data?.fallback_used && (
        <p
          className="mt-2 rounded border border-term-amber p-2 text-[11px] text-term-amber"
          role="note"
        >
          Client-side fallback — breadth computed from screener snapshots (backend
          /api/markets/overview not deployed). Figures may be delayed/partial; markets
          are NOT ranked.
        </p>
      )}
      <div className="mt-3 grid min-w-0 gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {markets.map((m) => (
          <MarketCard key={m.mic} m={m} />
        ))}
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        {data && <ProvenanceBadge p={data.provenance} />}
      </div>
      <p className="mt-2 text-[10px] text-term-muted">
        Breadth = advancers/decliners from latest screener change_pct per MIC. Not
        investment advice.
      </p>
    </section>
  );
}
