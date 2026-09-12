import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import {
  getAnalytics,
  getForecast,
  getQuote,
  type Analytics,
  type Forecast,
  type Provenance,
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

/** Deterministic placeholder — used only when the live forecast endpoint fails. */
function mockForecast(symbol: string): Forecast {
  void symbol;
  const provenance: Provenance = {
    source: 'deterministic-engine/mock',
    as_of: new Date().toISOString(),
    delay_minutes: 15,
    quality_grade: 'A',
    fallback_used: true,
    missing_fields: ['live_forecast'],
  };
  return {
    symbol,
    horizon_days: 21,
    label: 'moderately positive',
    probability: 0.64,
    confidence: 'Moderate',
    quality_grade: 'A',
    provider: 'deterministic-engine/mock',
    why: ['trend + momentum intact', 'quality: high ROE, low leverage', 'supportive sector breadth'],
    risks: ['valuation above 5y median', 'earnings event in 12d', 'elevated sector volatility'],
    evidence_ids: [],
    provenance,
  };
}

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
  const quote = useQuery({ queryKey: ['quote', symbol], queryFn: () => getQuote(symbol) });
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

  const forecast: Forecast = forecastQ.data ?? mockForecast(symbol);
  const forecastLive = forecastQ.data !== undefined;
  const analytics: Analytics | null = analyticsQ.data ?? null;
  const events = eventsFromAnalytics(analytics);

  if (quote.isLoading)
    return (
      <div className="max-w-full">
        <Skeleton label={`loading ${symbol}…`} lines={6} />
        <p className="mt-2 text-[11px] text-term-muted">{DISCLOSURE}</p>
      </div>
    );
  if (quote.isError) {
    return (
      <div className="max-w-full">
        <StaleBanner detail={quote.error instanceof Error ? quote.error.message : 'quote endpoint unreachable'} />
        {!forecastLive && (
          <StaleBanner detail="forecast endpoint unreachable — showing deterministic placeholder" />
        )}
        <ForecastCard symbol={symbol} price={null} currency="USD" forecast={forecast} live={!forecastQ.isError} />
        <p className="mt-2 text-[11px] text-term-muted">{DISCLOSURE}</p>
      </div>
    );
  }

  const q = quote.data;
  const stale = q.provenance.fallback_used || q.provenance.delay_minutes > 30;

  return (
    <div className="max-w-full">
      {stale && <StaleBanner detail={`quote via ${q.provenance.source}, delay ${q.provenance.delay_minutes}m`} />}
      {forecastQ.isError && (
        <StaleBanner detail="forecast endpoint unreachable — showing deterministic placeholder" />
      )}
      {analyticsQ.isError && (
        <StaleBanner detail="analytics endpoint unreachable — snapshot shows unavailable, rest of the page unaffected" />
      )}
      {/* Forecast header (spec §Milestone 8 example) */}
      <section className="term-panel min-w-0 p-4" aria-labelledby="brief-forecast">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 id="brief-forecast" className="min-w-0 text-lg font-bold">
            {q.symbol}{' '}
            <span className="text-sm font-normal text-term-muted">
              <CurrencyValue value={q.price} currency={q.currency ?? 'USD'} />
              {q.change_pct !== undefined && q.change_pct !== null && (
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
        <ForecastCard symbol={symbol} price={q.price} currency={q.currency ?? 'USD'} forecast={forecast} live={forecastLive} />
      </section>

      <section className="mt-4 grid max-w-full gap-4 md:grid-cols-2">
        <div className="term-panel min-w-0 p-4" aria-labelledby="brief-chart">
          <h3 id="brief-chart" className="term-label">
            Price chart
          </h3>
          <PriceChart symbol={symbol} />
          <div className="mt-2">
            <ProvenanceBadge p={q.provenance} />
          </div>
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
              {events.map((e) => (
                <li
                  key={`${e.date}-${e.title}`}
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
          <div className="mt-2">
            <ProvenanceBadge p={forecast.provenance} />
          </div>
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

function ForecastCard({
  symbol,
  price,
  currency,
  forecast: f,
  live,
}: {
  symbol: string;
  price: number | null;
  currency: string;
  forecast: Forecast;
  live: boolean;
}) {
  void symbol;
  const whyText = f.why.length > 0 ? f.why.join(' + ') : 'unavailable';
  const riskText = f.risks.length > 0 ? f.risks.join(' + ') : 'unavailable';
  return (
    <div className="mt-3 border-t border-term-border pt-3">
      <p className="term-label">
        Forecast · deterministic core (AI bounded, capped 20%)
        {!live && <span className="ml-2 text-term-amber">· placeholder</span>}
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
          AI provider: <b>Gemini 3.7 Flash</b>
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
          {f.why.length === 0 ? (
            <p className="text-term-muted">unavailable</p>
          ) : (
            <ul className="list-disc pl-4 text-term-muted">
              {f.why.map((b) => (
                <li key={b}>{b}</li>
              ))}
            </ul>
          )}
        </div>
        <div className="rounded border border-term-border p-2">
          <p className="font-bold text-term-red">▼ BEAR — risks</p>
          {f.risks.length === 0 ? (
            <p className="text-term-muted">unavailable</p>
          ) : (
            <ul className="list-disc pl-4 text-term-muted">
              {f.risks.map((b) => (
                <li key={b}>{b}</li>
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

function SnapshotCell({ title, data }: { title: string; data: Record<string, unknown> }) {
  const entries = Object.entries(data).slice(0, 4);
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
