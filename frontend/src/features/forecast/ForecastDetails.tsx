import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import {
  AI_PROFILES,
  FORECAST_HORIZONS,
  friendlyAIError,
  getAnalytics,
  getForecast,
  postAIInsight,
  type AIProfile,
  type Forecast,
} from '../../api/client';
import ProvenanceBadge from '../../components/ProvenanceBadge';
import FreshnessBadge from '../../components/FreshnessBadge';
import CalibrationChart from '../../components/CalibrationChart';
import AIOpinionCard from '../../components/AIOpinionCard';
import Loading from '../../components/Loading';
import { StaleBanner } from '../../components/ErrorState';

const DISCLOSURE =
  'Not investment advice. Forecasts are measurable probabilities from the deterministic engine; AI opinions are bounded and capped at 20% influence.';

function fmtPct(p: number): string {
  return `${(p * 100).toFixed(1)}%`;
}

export default function ForecastDetails({ symbol }: { symbol: string }) {
  const [horizon, setHorizon] = useState<number>(21);
  const [aiProfile, setAiProfile] = useState<AIProfile>('Forecast Assist');

  const forecastQ = useQuery({
    queryKey: ['forecast', symbol, horizon],
    queryFn: () => getForecast(symbol, horizon),
    retry: false,
    staleTime: 60_000,
  });
  const analyticsQ = useQuery({
    queryKey: ['analytics', symbol],
    queryFn: () => getAnalytics(symbol),
    retry: false,
    staleTime: 60_000,
  });
  const aiM = useMutation({
    mutationFn: () => postAIInsight(symbol, aiProfile),
  });

  // Honesty rule: no fabricated numbers. A missing/failed forecast renders an
  // explicit unavailable state — never a seeded placeholder figure.
  const live: Forecast | null = forecastQ.data ?? null;
  const f: Forecast | null = live;
  const analytics = analyticsQ.data ?? null;

  const dirWord = f ? (f.probability >= 0.5 ? 'bullish' : 'bearish') : undefined;

  return (
    <div className="space-y-4">
      {/* horizon switch */}
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="term-label">Horizon (trading days)</span>
        {FORECAST_HORIZONS.map((h) => (
          <button
            key={h}
            className={h === horizon ? 'term-btn' : 'term-btn-ghost'}
            onClick={() => setHorizon(h)}
          >
            {h}d
          </button>
        ))}
        <span className="text-term-muted">targets: direction probability · return range · vol regime · drawdown</span>
      </div>

      {forecastQ.isLoading && <Loading label={`loading forecast ${symbol} ${horizon}d…`} />}
      {forecastQ.isError && (
        <StaleBanner
          detail={`forecast endpoint unreachable (${forecastQ.error instanceof Error ? forecastQ.error.message : 'unknown error'}) — forecast unavailable, no placeholder numbers shown`}
        />
      )}
      {!forecastQ.isError && !forecastQ.isLoading && !live && (
        <StaleBanner detail="live forecast not yet returned — forecast unavailable, no placeholder numbers shown" />
      )}
      {f && (f.provenance.fallback_used || f.provenance.delay_minutes > 30) && !forecastQ.isError && (
        <StaleBanner
          detail={`forecast via ${f.provenance.source}, delay ${f.provenance.delay_minutes}m`}
        />
      )}

      {/* header */}
      {!f && !forecastQ.isLoading && (
        <section className="term-panel p-4" role="status">
          <p className="term-label">Forecast · deterministic engine</p>
          <p className="mt-2 text-sm text-term-muted">
            Forecast unavailable for {symbol} at {horizon}d — the forecast endpoint is
            unreachable or returned no data. No placeholder numbers are shown.
          </p>
          <button
            className="term-btn-ghost mt-3 text-xs"
            type="button"
            onClick={() => void forecastQ.refetch()}
          >
            RETRY FORECAST
          </button>
        </section>
      )}
      {f && (
      <section className="term-panel p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-lg font-bold">
            Forecast: {f.label}, {f.horizon_days} days{' '}
            <FreshnessBadge p={f.provenance} />
          </h2>
        </div>
        <p className="mt-1 text-2xl font-bold text-term-green">
          {fmtPct(f.probability)} <ProvenanceBadge p={f.provenance} />
        </p>
        <dl className="mt-2 grid grid-cols-2 gap-2 text-xs md:grid-cols-4">
          <div>
            Confidence: <b>{f.confidence}</b> <ProvenanceBadge p={f.provenance} />
          </div>
          <div>
            Data quality: <b className="text-term-cyan">{f.quality_grade}</b>{' '}
            <ProvenanceBadge p={f.provenance} />
          </div>
          <div>
            Provider: <b>{f.provider}</b>
          </div>
          <div>
            Horizon: <b>{f.horizon_days}d</b>
          </div>
        </dl>
        <div className="mt-2 grid gap-2 text-xs md:grid-cols-2">
          <div className="rounded border border-term-border p-2">
            <p className="font-bold text-term-green">Why (bullish drivers)</p>
            {f.why.length === 0 ? (
              <p className="text-term-muted">unavailable</p>
            ) : (
              <ul className="list-disc pl-4 text-term-muted">
                {f.why.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            )}
          </div>
          <div className="rounded border border-term-border p-2">
            <p className="font-bold text-term-red">Risks (bearish drivers)</p>
            {f.risks.length === 0 ? (
              <p className="text-term-muted">unavailable</p>
            ) : (
              <ul className="list-disc pl-4 text-term-muted">
                {f.risks.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </section>
      )}

      {/* inputs / evidence / versions */}
      {f && (
      <section className="term-panel p-4">
        <p className="term-label">Inputs · evidence · versions</p>
        <div className="mt-2 grid gap-2 text-xs md:grid-cols-3">
          <div className="rounded border border-term-border p-2">
            <p className="font-bold">Model versions</p>
            <ul className="mt-1 space-y-0.5 text-term-muted">
              {f.versions ? (
                Object.entries(f.versions).map(([k, v]) => (
                  <li key={k}>
                    {k}: <b className="text-term-text">{String(v)}</b>
                  </li>
                ))
              ) : (
                <li>unavailable</li>
              )}
            </ul>
          </div>
          <div className="rounded border border-term-border p-2">
            <p className="font-bold">Evidence IDs</p>
            {f.evidence_ids.length === 0 ? (
              <p className="mt-1 text-term-muted">none</p>
            ) : (
              <p className="mt-1">
                {f.evidence_ids.map((e) => (
                  <code key={e} className="mr-1 rounded bg-term-bg px-1 py-0.5 text-term-cyan">
                    {e}
                  </code>
                ))}
              </p>
            )}
            {f.inputs && (
              <ul className="mt-2 space-y-0.5 text-term-muted">
                {Object.entries(f.inputs).map(([k, v]) => (
                  <li key={k}>
                    {k}: <span className="text-term-text">{String(v)}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div className="rounded border border-term-border p-2">
            <p className="font-bold">Intervals (expected return)</p>
            {f.intervals ? (
              <p className="mt-1 text-term-muted">
                low <b className="text-term-red">{(f.intervals.low * 100).toFixed(1)}%</b> · mid{' '}
                <b className="text-term-text">{(f.intervals.mid * 100).toFixed(1)}%</b> · high{' '}
                <b className="text-term-green">{(f.intervals.high * 100).toFixed(1)}%</b>{' '}
                <ProvenanceBadge p={f.provenance} />
              </p>
            ) : (
              <p className="mt-1 text-term-muted">unavailable</p>
            )}
          </div>
        </div>
        <div className="mt-2">
          <ProvenanceBadge p={f.provenance} />
        </div>
      </section>
      )}

      {/* calibration */}
      <section className="term-panel p-4">
        <CalibrationChart rows={f?.calibration ?? []} title={`Calibration history · ${f?.horizon_days ?? horizon}d`} />
        {f && f.calibration.length > 0 ? (
          <div className="mt-2 overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-term-muted">
                  <th className="py-1 pr-2">Bin</th>
                  <th className="py-1 pr-2">n</th>
                  <th className="py-1 pr-2">Mean predicted</th>
                  <th className="py-1 pr-2">Fraction positive</th>
                </tr>
              </thead>
              <tbody>
                {f.calibration.map((r, i) => (
                  <tr key={i} className="border-t border-term-border">
                    <td className="py-1 pr-2">
                      {r.bin_low.toFixed(2)}–{r.bin_high.toFixed(2)}
                    </td>
                    <td className="py-1 pr-2">{r.count}</td>
                    <td className="py-1 pr-2">
                      {typeof r.mean_predicted === 'number' && Number.isFinite(r.mean_predicted)
                        ? r.mean_predicted.toFixed(3)
                        : '—'}
                    </td>
                    <td className="py-1 pr-2">
                      {typeof r.fraction_positive === 'number' && Number.isFinite(r.fraction_positive)
                        ? r.fraction_positive.toFixed(3)
                        : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="mt-2 text-xs text-term-muted">
            No calibration bins for this horizon yet — walk-forward history lands with the M3
            engine; the Backtest Lab shows failures as well as successes.
          </p>
        )}
        {f && (
          <div className="mt-2">
            <ProvenanceBadge p={f.provenance} />
          </div>
        )}
      </section>

      {/* analytics snapshot */}
      <section className="term-panel p-4">
        <p className="term-label">Deterministic analytics snapshot</p>
        {analytics?.note && (
          <p className="mt-1 text-[11px] text-term-amber" role="note">
            {analytics.note}
          </p>
        )}
        {analyticsQ.isLoading && <p className="mt-1 text-xs text-term-muted">loading analytics…</p>}
        {analyticsQ.isError && (
          <p className="mt-1 text-xs text-term-amber">
            ⚠ analytics endpoint unreachable — snapshot unavailable, forecast above unaffected.
          </p>
        )}
        {analytics && (
          <>
            <AnalyticsGrid title="Technical" data={analytics.technical} />
            <AnalyticsGrid title="Fundamentals" data={analytics.fundamentals} />
            <AnalyticsGrid title="Quality" data={analytics.quality} />
            <AnalyticsGrid title="Valuation" data={analytics.valuation} />
            <div className="mt-2 flex flex-wrap gap-2">
              <ProvenanceBadge p={analytics.provenance} />
              <FreshnessBadge p={analytics.provenance} />
            </div>
          </>
        )}
        {!analytics && !analyticsQ.isLoading && !analyticsQ.isError && (
          <p className="mt-1 text-xs text-term-muted">No analytics payload yet.</p>
        )}
      </section>

      {/* limitations + disclosure */}
      <section className="term-panel p-4">
        <p className="term-label">Limitations</p>
        {f && f.limitations.length > 0 ? (
          <ul className="mt-1 list-disc pl-5 text-sm text-term-muted">
            {f.limitations.map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
        ) : (
          <p className="mt-1 text-sm text-term-muted">unavailable — live forecast not loaded.</p>
        )}
        <div className="mt-3 rounded border border-term-amber bg-term-panel p-3 text-xs text-term-amber">
          {f?.disclosure ?? DISCLOSURE}
        </div>
      </section>

      {/* AI opinion (explicit request only) */}
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <label className="term-label" htmlFor="ai-profile">
          AI profile
        </label>
        <select
          id="ai-profile"
          className="term-input"
          value={aiProfile}
          onChange={(e) => setAiProfile(e.target.value as AIProfile)}
        >
          {AI_PROFILES.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
        <button
          className="term-btn"
          disabled={aiM.isPending}
          onClick={() => aiM.mutate()}
        >
          {aiM.isPending ? 'REQUESTING…' : aiM.data ? 'REFRESH AI OPINION' : 'REQUEST AI OPINION'}
        </button>
        {aiM.isError && (
          <span className="text-term-amber">
            ⚠ {friendlyAIError(aiM.error)}
            — deterministic forecast above is unaffected.
          </span>
        )}
      </div>
      <AIOpinionCard
        opinion={aiM.data ?? null}
        deterministicProbability={f?.probability}
        deterministicDirection={dirWord}
        provenance={f?.provenance}
        onRequest={aiM.data ? undefined : () => aiM.mutate()}
        requesting={aiM.isPending}
        requestError={aiM.isError ? friendlyAIError(aiM.error) : null}
      />
    </div>
  );
}

function AnalyticsGrid({ title, data }: { title: string; data: Record<string, unknown> }) {
  const entries = Object.entries(data);
  return (
    <div className="mt-2">
      <p className="text-xs font-bold text-term-text">{title}</p>
      {entries.length === 0 ? (
        <p className="text-xs text-term-muted">unavailable</p>
      ) : (
        <dl className="mt-1 grid grid-cols-2 gap-1 text-xs md:grid-cols-4">
          {entries.slice(0, 12).map(([k, v]) => (
            <div key={k} className="rounded border border-term-border px-2 py-1">
              <dt className="text-term-muted">{k}</dt>
              <dd className="font-bold">{typeof v === 'object' ? JSON.stringify(v) : String(v)}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}
