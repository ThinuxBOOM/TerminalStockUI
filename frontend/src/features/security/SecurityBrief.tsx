import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import {
  getAnalytics,
  getBars,
  getForecast,
  getQuote,
  type Analytics,
  type Forecast,
} from '../../api/client';
import ProvenanceBadge from '../../components/ProvenanceBadge';
import FreshnessBadge from '../../components/FreshnessBadge';
import MarketStateBadge from '../../components/MarketStateBadge';
import CurrencyValue from '../../components/CurrencyValue';
import Loading from '../../components/Loading';
import Skeleton from '../../components/Skeleton';
import ErrorState, { StaleBanner } from '../../components/ErrorState';
import PriceChart from './PriceChart';

const DISCLOSURE = 'Not investment advice. For informational purposes only.';

type BriefEvent = { date: string; title: string };

/** Events timeline from getAnalytics (tolerant) — missing → [] → "unavailable". */
function eventsFromAnalytics(a: Analytics | null): BriefEvent[] {
  if (!a) return [];
  const raw = (a as unknown as Record<string, unknown>).events;
  if (!Array.isArray(raw)) return [];
  const out: BriefEvent[] = [];
  for (const e of raw) {
    if (typeof e === 'string') {
      out.push({ date: '', title: e });
      continue;
    }
    if (e && typeof e === 'object') {
      const r = e as Record<string, unknown>;
      const title = String(r.title ?? r.event ?? r.name ?? '').trim();
      if (!title) continue;
      const date = String(r.date ?? r.ts ?? r.as_of ?? '').trim();
      out.push({ date, title });
    }
  }
  return out.slice(0, 12);
}

export default function SecurityBrief({ symbol }: { symbol: string }) {
  const quote = useQuery({
    queryKey: ['quote', symbol],
    queryFn: () => getQuote(symbol),
    retry: false,
    staleTime: 30_000,
  });
  const forecastQ = useQuery({
    queryKey: ['forecast', symbol, 21],
    queryFn: () => getForecast(symbol, 21),
    retry: false,
    staleTime: 60_000,
  });
  const analyticsQ = useQuery({
    queryKey: ['analytics', symbol],
    queryFn: () => getAnalytics(symbol),
    retry: false,
    staleTime: 60_000,
  });
  const barsQ = useQuery({
    queryKey: ['bars', symbol],
    queryFn: () => getBars(symbol, '1d', 90),
    retry: false,
    staleTime: 60_000,
  });

  // Honesty rule: no fabricated numbers. A missing/failed forecast renders an
  // explicit unavailable state — never a seeded placeholder figure.
  const forecast: Forecast | null = forecastQ.data ?? null;
  const analytics: Analytics | null = analyticsQ.data ?? null;
  const events = useMemo(() => eventsFromAnalytics(analytics), [analytics]);
  // Non-blocking: the quote header skeletons inline while forecast, chart
  // and events (already fetching in parallel) render from their own
  // queries instead of waiting behind `quote.isLoading`.
  const quoteLoading = quote.isLoading && !quote.data;
  if (quote.isError) {
    return (
      <div className="max-w-full">
        <StaleBanner detail={quote.error instanceof Error ? quote.error.message : 'quote endpoint unreachable'} />
        <ForecastUnavailable
          detail="forecast not requested without a live quote — no placeholder numbers shown"
          onRetry={() => {
            void quote.refetch();
            void forecastQ.refetch();
          }}
        />
        <p className="mt-2 text-[11px] text-term-muted">{DISCLOSURE}</p>
      </div>
    );
  }

  const q = quote.data;
  if (!q && !quoteLoading)
    return (
      <div className="max-w-full">
        <ErrorState
          title="Security Brief unavailable"
          detail={`No quote payload for ${symbol} — the quote endpoint returned empty.`}
          onRetry={() => {
            void quote.refetch();
            void forecastQ.refetch();
          }}
        />
        <p className="mt-2 text-[11px] text-term-muted">{DISCLOSURE}</p>
      </div>
    );
  if (!q)
    return (
      <div className="max-w-full">
        <Skeleton label={`loading ${symbol}…`} lines={3} />
        {forecastQ.isLoading && (
          <div className="mt-4">
            <Skeleton label="loading live forecast…" lines={4} />
          </div>
        )}
        {!forecastQ.isLoading && forecast && (
          <div className="term-panel mt-4 min-w-0 p-4">
            <ForecastCard price={null} currency="USD" forecast={forecast} />
          </div>
        )}
        <div className="term-panel mt-4 min-w-0 p-4" aria-labelledby="brief-chart">
          <h3 id="brief-chart" className="term-label">
            Price chart
          </h3>
          <PriceChart
            symbol={symbol}
            data={barsQ.data?.candles ?? null}
            loading
            error={null}
            provenance={barsQ.data?.provenance ?? null}
          />
        </div>
        <p className="mt-2 text-[11px] text-term-muted">{DISCLOSURE}</p>
      </div>
    );
  const stale = q.provenance?.fallback_used === true || (q.provenance?.delay_minutes ?? 0) > 30;

  return (
    <div className="max-w-full">
      {stale && <StaleBanner detail={`quote via ${q.provenance?.source ?? 'unknown'}, delay ${q.provenance?.delay_minutes ?? '—'}m`} />}
      {forecastQ.isError && (
        <StaleBanner detail={`forecast endpoint unreachable (${forecastQ.error instanceof Error ? forecastQ.error.message : 'unknown error'}) — forecast unavailable, no placeholder numbers shown`} />
      )}
      {analyticsQ.isError && (
        <StaleBanner detail="analytics endpoint unreachable — snapshot shows unavailable, rest of the page unaffected" />
      )}
      {barsQ.isError && (
        <StaleBanner detail={`price history unreachable (${barsQ.error instanceof Error ? barsQ.error.message : 'bars endpoint error'}) — chart shows unavailable, rest of the page unaffected`} />
      )}
      {/* Forecast header (spec §Milestone 8 example) */}
      <section className="term-panel min-w-0 p-4" aria-labelledby="brief-forecast">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 id="brief-forecast" className="min-w-0 text-lg font-bold">
            {q.symbol}{' '}
            <span className="text-sm font-normal text-term-muted">
              <CurrencyValue value={q.price} currency={q.currency ?? 'USD'} />
              {typeof q.change_pct === 'number' && Number.isFinite(q.change_pct) && (
                <span className={q.change_pct >= 0 ? 'text-term-green' : 'text-term-red'}>
                  {' '}
                  ({q.change_pct >= 0 ? '+' : ''}
                  {q.change_pct.toFixed(2)}%)
                </span>
              )}
            </span>
          </h2>
          <div className="flex flex-wrap items-center gap-2">
            <MarketStateBadge state={q.market_state} provenance={q.provenance} />
            <FreshnessBadge p={q.provenance} />
          </div>
        </div>
        {q.ambiguous && (
          <div
            className="mt-2 rounded border border-term-amber p-2 text-xs text-term-amber"
            role="status"
          >
            Ambiguous symbol — showing {q.symbol}
            {(q.candidates ?? []).length > 0 &&
              `; other candidates: ${(q.candidates ?? []).join(', ')}`}
            . Not auto-resolved; refine with the full provider symbol (e.g. 600519.SS).
          </div>
        )}
        <div className="mt-1">
          <ProvenanceBadge p={q.provenance} />
        </div>
        {forecastQ.isLoading && (
          <div className="mt-3">
            <Skeleton label="loading live forecast…" lines={4} />
          </div>
        )}
        {!forecastQ.isLoading && forecast && (
          <ForecastCard price={q.price} currency={q.currency ?? 'USD'} forecast={forecast} />
        )}
        {!forecastQ.isLoading && !forecast && (
          <ForecastUnavailable
            detail="live forecast unreachable — no placeholder numbers shown"
            onRetry={() => void forecastQ.refetch()}
          />
        )}
      </section>

      <section className="mt-4 grid max-w-full gap-4 md:grid-cols-2">
        <div className="term-panel min-w-0 p-4" aria-labelledby="brief-chart">
          <h3 id="brief-chart" className="term-label">
            Price chart
          </h3>
          <PriceChart
            symbol={symbol}
            data={barsQ.data?.candles ?? null}
            loading={barsQ.isLoading || barsQ.isFetching}
            error={
              barsQ.isError
                ? barsQ.error instanceof Error
                  ? barsQ.error.message
                  : 'bars endpoint unreachable'
                : null
            }
            provenance={barsQ.data?.provenance ?? null}
          />
        </div>
        <div className="term-panel min-w-0 p-4" aria-labelledby="brief-events">
          <h3 id="brief-events" className="term-label">
            Events timeline
          </h3>
          {analyticsQ.isLoading && (
            <div className="mt-2">
              <Skeleton label="loading events…" lines={3} />
            </div>
          )}
          {!analyticsQ.isLoading && events.length > 0 && (
            <ul className="mt-2 space-y-2 text-sm">
              {events.map((e, i) => (
                <li
                  key={`${e.date}-${e.title}-${i}`}
                  className="flex justify-between gap-2 border-b border-term-border pb-1"
                >
                  <span className="min-w-0">{e.title}</span>
                  <span className="shrink-0 text-term-muted">{e.date || '—'}</span>
                </li>
              ))}
            </ul>
          )}
          {!analyticsQ.isLoading && events.length === 0 && (
            <p className="mt-2 text-xs text-term-muted" role="status">
              Events unavailable — no event feed for this symbol yet.
            </p>
          )}
          {analytics && (
            <div className="mt-2">
              <ProvenanceBadge p={analytics.provenance} />
            </div>
          )}
          <Link to={`/forecast/${encodeURIComponent(symbol)}`} className="term-btn-ghost mt-3 inline-block text-xs">
            FORECAST DETAILS →
          </Link>
        </div>
      </section>

      <section className="mt-4">
        <AnalyticsSnapshot analytics={analytics} loading={analyticsQ.isLoading} failed={analyticsQ.isError} />
      </section>

      <p className="mt-3 text-[11px] text-term-muted">{DISCLOSURE}</p>
    </div>
  );
}

function ForecastUnavailable({ detail, onRetry }: { detail: string; onRetry?: () => void }) {
  return (
    <div className="mt-3 rounded border border-term-border p-4" role="status">
      <p className="term-label">Forecast · deterministic core (AI bounded, capped 20%)</p>
      <p className="mt-2 text-sm text-term-muted">Forecast unavailable — {detail}.</p>
      {onRetry && (
        <button className="term-btn-ghost mt-3 text-xs" type="button" onClick={onRetry}>
          RETRY FORECAST
        </button>
      )}
    </div>
  );
}

function ForecastCard({
  price,
  currency,
  forecast: f,
}: {
  price: number | null;
  currency: string;
  forecast: Forecast;
}) {
  const why = f.why ?? [];
  const risks = f.risks ?? [];
  const whyText = why.length > 0 ? why.join(' + ') : 'unavailable';
  const riskText = risks.length > 0 ? risks.join(' + ') : 'unavailable';
  return (
    <div className="mt-3 border-t border-term-border pt-3">
      <p className="term-label">
        Forecast · deterministic core (AI bounded, capped 20%)
      </p>
      {/* Spec M8 example header — labels below match verbatim. */}
      <div className="mt-1 space-y-1 text-sm">
        <p>
          Forecast:{' '}
          <b className="text-term-text">
            {f.label}, {f.horizon_days} days
          </b>{' '}
          <FreshnessBadge p={f.provenance} />
        </p>
        <p className="text-2xl font-bold text-term-green">
          Probability: {(f.probability * 100).toFixed(0)}% <ProvenanceBadge p={f.provenance} />
        </p>
        <p className="text-xs">
          Confidence: <b>{f.confidence}</b>
        </p>
        <p className="text-xs">
          Data quality: <b className="text-term-cyan">{f.quality_grade}</b>
        </p>
        <p className="text-xs">
          AI provider:{' '}
          <b>{f.provider && f.provider !== 'deterministic-engine' ? f.provider : 'none — deterministic core'}</b>
        </p>
        <p className="text-xs">
          Why: <span className="text-term-muted">{whyText}</span>
        </p>
        <p className="text-xs">
          Risks: <span className="text-term-muted">{riskText}</span>
        </p>
      </div>
      <dl className="mt-2 grid grid-cols-2 gap-2 text-xs md:grid-cols-4">
        <div>
          Last price:{' '}
          <b>
            <CurrencyValue value={price} currency={currency} />
          </b>
        </div>
        <div>
          Horizon: <b>{f.horizon_days}d</b>
        </div>
      </dl>
      <div className="mt-1">
        <ProvenanceBadge p={f.provenance} />
      </div>
      <div className="mt-2 grid gap-2 text-xs md:grid-cols-2">
        <div className="rounded border border-term-border p-2">
          <p className="font-bold text-term-green">▲ BULL — why</p>
          {why.length === 0 ? (
            <p className="text-term-muted">unavailable</p>
          ) : (
            <ul className="list-disc pl-4 text-term-muted">
              {why.map((b, i) => (
                <li key={`${b}-${i}`}>{b}</li>
              ))}
            </ul>
          )}
        </div>
        <div className="rounded border border-term-border p-2">
          <p className="font-bold text-term-red">▼ BEAR — risks</p>
          {risks.length === 0 ? (
            <p className="text-term-muted">unavailable</p>
          ) : (
            <ul className="list-disc pl-4 text-term-muted">
              {risks.map((b, i) => (
                <li key={`${b}-${i}`}>{b}</li>
              ))}
            </ul>
          )}
        </div>
      </div>
      <p className="mt-2 text-[11px] text-term-muted">Not investment advice.</p>
    </div>
  );
}

function AnalyticsSnapshot({
  analytics,
  loading,
  failed,
}: {
  analytics: Analytics | null;
  loading: boolean;
  failed: boolean;
}) {
  return (
    <div className="term-panel min-w-0 p-4" aria-labelledby="brief-analytics">
      <h3 id="brief-analytics" className="term-label">
        Analytics snapshot · deterministic
      </h3>
      {analytics?.note && (
        <p className="mt-1 text-[11px] text-term-amber" role="note">
          {analytics.note}
        </p>
      )}
      {loading && (
        <div className="mt-2">
          <Skeleton label="loading analytics…" lines={4} />
        </div>
      )}
      {failed && (
        <p className="mt-1 text-xs text-term-amber" role="alert">
          ⚠ analytics endpoint unreachable — snapshot unavailable, rest of the page unaffected.
        </p>
      )}
      {!loading && !failed && !analytics && (
        <p className="mt-1 text-xs text-term-muted" role="status">
          No analytics payload yet.
        </p>
      )}
      {analytics && (
        <>
          <dl className="mt-2 grid grid-cols-2 gap-2 text-xs md:grid-cols-4">
            <SnapshotCell title="Technical" data={analytics.technical} />
            <SnapshotCell title="Fundamentals" data={analytics.fundamentals} />
            <SnapshotCell title="Quality" data={analytics.quality} />
            <SnapshotCell title="Valuation" data={analytics.valuation} />
          </dl>
          <div className="mt-2 flex flex-wrap gap-2">
            <ProvenanceBadge p={analytics.provenance} />
            <FreshnessBadge p={analytics.provenance} />
          </div>
        </>
      )}
      {!analytics && failed && (
        <div className="mt-2 grid grid-cols-2 gap-2 text-xs md:grid-cols-4">
          {['Technical', 'Fundamentals', 'Quality', 'Valuation'].map((t) => (
            <div key={t} className="rounded border border-term-border p-2">
              <p className="font-bold">{t}</p>
              <p className="text-term-muted">unavailable</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function SnapshotCell({ title, data }: { title: string; data: Record<string, unknown> | null | undefined }) {
  const all = Object.entries(data ?? {});
  const entries = all.slice(0, 4);
  return (
    <div className="min-w-0 rounded border border-term-border p-2">
      <p className="font-bold">{title}</p>
      {entries.length === 0 ? (
        <p className="text-term-muted">unavailable</p>
      ) : (
        <ul className="mt-1 space-y-0.5 break-words text-term-muted">
          {entries.map(([k, v]) => (
            <li key={k}>
              {k}: <b className="text-term-text">{typeof v === 'object' ? JSON.stringify(v) : String(v)}</b>
            </li>
          ))}
          {all.length > entries.length && (
            <li className="text-[10px]" role="status">
              +{all.length - entries.length} more
            </li>
          )}
        </ul>
      )}
    </div>
  );
}

export function SecurityBriefError({ onRetry }: { onRetry?: () => void }) {
  return <ErrorState title="Security Brief failed" detail="Quote + forecast both unreachable." onRetry={onRetry} />;
}

export function SecurityBriefLoading({ symbol }: { symbol: string }) {
  return (
    <div>
      <Loading label={`loading ${symbol}…`} />
      <p className="mt-2 text-[11px] text-term-muted">Not investment advice.</p>
    </div>
  );
}
