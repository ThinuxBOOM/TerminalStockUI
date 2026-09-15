import React, { Suspense, lazy, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { TIMEFRAME_PRESETS, SUPPORTED_INDICATORS, getAnalytics, getBars, getForecast, getQuote, auditForecastsUrl, loadFavoriteIndicators, resolveTimeframePreset, saveFavoriteIndicators } from "../../api/client";
import ProvenanceBadge from "../../components/ProvenanceBadge";
import FreshnessBadge from "../../components/FreshnessBadge";
import MarketStateBadge from "../../components/MarketStateBadge";
import CurrencyValue from "../../components/CurrencyValue";
import Loading from "../../components/Loading";
import Skeleton from "../../components/Skeleton";
import ErrorState, { StaleBanner } from "../../components/ErrorState";
import { SourceBadge, BlendedForecastBar, DeepResearchStub } from "../../components/ResearchSection";

// PriceChart (and lightweight-charts) loads on demand — the brief header,
// forecast and events render without waiting for chart code.
const PriceChart = lazy(() => import("./PriceChart"));

const DISCLOSURE = "Not investment advice. For informational purposes only.";

function eventsFromAnalytics(a) {
  if (!a) return [];
  const raw = a.events;
  if (!Array.isArray(raw)) return [];
  const out = [];
  for (const e of raw) {
    if (typeof e === "string") {
      out.push({ date: "", title: e });
      continue;
    }
    if (e && typeof e === "object") {
      const r = e;
      const title = String(r.title ?? r.event ?? r.name ?? "").trim();
      if (!title) continue;
      const date = String(r.date ?? r.ts ?? r.as_of ?? "").trim();
      out.push({ date, title });
    }
  }
  return out.slice(0, 12);
}

// ChartControls — timeframe presets (1D/1W/1M/3M/1Y/2Y/5Y, each wired to a
// bars-API limit of daily candles) + indicator multi-select. Selection fetches overlay
// series via GET /api/analytics?indicators=... and persists to the per-user
// favorites stub (localStorage `indicators:<userId||guest>`, no auth yet).
function ChartControls({ preset, onTimeframe, selected, onToggle }) {
  return (
    <div>
      <div className="mt-2 flex flex-wrap items-center gap-1" role="group" aria-label="Chart timeframe">
        {TIMEFRAME_PRESETS.map((p) => (
          <button
            key={p.id}
            type="button"
            onClick={() => onTimeframe(p.id)}
            aria-pressed={preset.id === p.id}
            title={`${p.id} — last ${p.limit} daily bars`}
            className={preset.id === p.id ? "rounded border border-term-green px-2 py-0.5 text-[10px] font-bold text-term-green" : "rounded border border-term-border px-2 py-0.5 text-[10px] text-term-muted"}
          >
            {p.label}
          </button>
        ))}
      </div>
      <div className="mt-2">
        <p className="text-[10px] text-term-muted">Overlays — series from the analytics indicator API; legend toggles live on the chart</p>
        <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1">
          {SUPPORTED_INDICATORS.map((name) => (
            <label key={name} className="inline-flex cursor-pointer items-center gap-1 text-[11px] text-term-text">
              <input type="checkbox" checked={selected.includes(name)} onChange={() => onToggle(name)} aria-label={`overlay ${name}`} />
              {name}
            </label>
          ))}
        </div>
      </div>
    </div>
  );
}

function SecurityBrief({ symbol }) {
  // Chart timeframe preset: each maps to a bars-API limit (daily candles;
  // see TIMEFRAME_PRESETS). Backend serves limit<=1000, so 1Y=250, 2Y=500,
  // 5Y=1000 (chart decimates to 500 for display, extremes preserved).
  const [timeframeId, setTimeframeId] = useState("3M");
  const preset = resolveTimeframePreset(timeframeId);
  // Indicator overlays, init'd from the favorites stub (guest namespace until
  // auth lands), persisted on every toggle.
  const [selectedIndicators, setSelectedIndicators] = useState(() =>
    loadFavoriteIndicators(undefined, ["SMA20", "EMA12", "RSI14"])
  );
  function toggleIndicator(name) {
    setSelectedIndicators((prev) => {
      const next = prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name];
      saveFavoriteIndicators(undefined, next);
      return next;
    });
  }
  const quote = useQuery({
    queryKey: ["quote", symbol],
    queryFn: ({ signal }) => getQuote(symbol, undefined, { signal }),
    retry: false,
    staleTime: 30000,
  });
  const forecastQ = useQuery({
    queryKey: ["forecast", symbol, 21],
    queryFn: ({ signal }) => getForecast(symbol, 21, { signal }),
    retry: false,
    staleTime: 60000,
  });
  const analyticsQ = useQuery({
    queryKey: ["analytics", symbol, selectedIndicators.join(",")],
    queryFn: ({ signal }) => getAnalytics(symbol, { signal, indicators: selectedIndicators }),
    retry: false,
    staleTime: 60000,
  });
  const barsQ = useQuery({
    queryKey: ["bars", symbol, preset.timeframe, preset.limit],
    queryFn: ({ signal }) => getBars(symbol, preset.timeframe, preset.limit, { signal }),
    retry: false,
    staleTime: 60000,
  });
  const forecast = forecastQ.data ?? null;
  const analytics = analyticsQ.data ?? null;
  const events = useMemo(() => eventsFromAnalytics(analytics), [analytics]);
  const q = quote.data;
  // Non-blocking quote: the header skeletons inline while forecast, chart
  // and events (already fetching in parallel) render from their own queries.
  const quoteLoading = quote.isLoading && !q;
  if (quoteLoading) {
    return (
      <div className="max-w-full">
        <Skeleton label={`loading ${symbol}…`} lines={3} />
        {forecast && (
          <div className="term-panel mt-4 min-w-0 p-4">
            <ForecastCard price={null} currency="USD" forecast={forecast} symbol={symbol} />
          </div>
        )}
        <div className="term-panel mt-4 min-w-0 p-4">
          <h3 className="term-label">Price chart</h3>
          <Suspense fallback={<Skeleton label="loading chart..." lines={4} />}>
            <PriceChart
              symbol={symbol}
              data={barsQ.data?.candles ?? null}
              loading
              error={barsQ.isError ? (barsQ.error instanceof Error ? barsQ.error.message : "bars endpoint unreachable") : null}
              provenance={barsQ.data?.provenance ?? null}
              indicators={analyticsQ.data?.indicators ?? {}}
              requestedIndicators={selectedIndicators}
              indicatorsLoading={analyticsQ.isLoading || analyticsQ.isFetching}
              indicatorsError={analyticsQ.isError ? (analyticsQ.error instanceof Error ? analyticsQ.error.message : "analytics endpoint unreachable") : null}
              indicatorsProvenance={analyticsQ.data?.provenance ?? null}
              currency="USD"
              onRetryIndicators={() => void analyticsQ.refetch()}
            />
          </Suspense>
        </div>
        <p className="mt-2 text-[11px] text-term-muted">{DISCLOSURE}</p>
      </div>
    );
  }
  if (quote.isError) {
    // Hard error with no quote payload: ErrorState (with retry), not a
    // "cached data" banner — nothing is being shown.
    return (
      <div className="max-w-full">
        <ErrorState
          title="Quote unavailable"
          detail={quote.error instanceof Error ? quote.error.message : "quote endpoint unreachable"}
          onRetry={() => {
            void quote.refetch();
            void forecastQ.refetch();
          }}
        />
        <p className="mt-2 text-[11px] text-term-muted">{DISCLOSURE}</p>
      </div>
    );
  }
  if (!q) {
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
  }
  const stale = q.provenance?.fallback_used === true || (q.provenance?.delay_minutes ?? 0) > 30;
  return (
    <div className="max-w-full">
      {stale && <StaleBanner detail={`quote via ${q.provenance?.source ?? "unknown"}, delay ${q.provenance?.delay_minutes ?? "—"}m`} />}
      {forecastQ.isError && (
        <StaleBanner detail={`forecast endpoint unreachable (${forecastQ.error instanceof Error ? forecastQ.error.message : "unknown error"}) — forecast unavailable, no placeholder numbers shown`} />
      )}
      {analyticsQ.isError && <StaleBanner detail="analytics endpoint unreachable — snapshot shows unavailable, rest of the page unaffected" />}
      {barsQ.isError && (
        <StaleBanner detail={`price history unreachable (${barsQ.error instanceof Error ? barsQ.error.message : "bars endpoint error"}) — chart shows unavailable, rest of the page unaffected`} />
      )}
      <section className="term-panel min-w-0 p-4" aria-labelledby="brief-forecast">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 id="brief-forecast" className="min-w-0 text-lg font-bold">
            {q.symbol}{" "}
            <span className="text-sm font-normal text-term-muted">
              <CurrencyValue value={q.price} currency={q.currency ?? "USD"} />
              {typeof q.change_pct === "number" && Number.isFinite(q.change_pct) && (
                <span className={q.change_pct >= 0 ? "text-term-green" : "text-term-red"}>
                  {" "}({q.change_pct >= 0 ? "+" : ""}{q.change_pct.toFixed(2)}%)
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
          <div className="mt-2 rounded border border-term-amber p-2 text-xs text-term-amber" role="status">
            Ambiguous symbol — showing {q.symbol}
            {(q.candidates ?? []).length > 0 && `; other candidates: ${(q.candidates ?? []).join(", ")}`}. Not auto-resolved; refine with the full provider symbol (e.g. 600519.SS).
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
        {!forecastQ.isLoading && forecast && <ForecastCard price={q.price} currency={q.currency ?? "USD"} forecast={forecast} symbol={symbol} />}
        {!forecastQ.isLoading && !forecast && (
          <ForecastUnavailable detail="live forecast unreachable — no placeholder numbers shown" onRetry={() => void forecastQ.refetch()} />
        )}
      </section>
      <section className="mt-4 grid max-w-full gap-4 md:grid-cols-2">
        <div className="term-panel min-w-0 p-4" aria-labelledby="brief-chart">
          <h3 id="brief-chart" className="term-label">Price chart</h3>
          <ChartControls preset={preset} onTimeframe={setTimeframeId} selected={selectedIndicators} onToggle={toggleIndicator} />
          <Suspense fallback={<Skeleton label="loading chart…" lines={4} />}>
            <PriceChart
              symbol={symbol}
              data={barsQ.data?.candles ?? null}
              loading={barsQ.isLoading || barsQ.isFetching}
              error={barsQ.isError ? (barsQ.error instanceof Error ? barsQ.error.message : "bars endpoint unreachable") : null}
              provenance={barsQ.data?.provenance ?? null}
              indicators={analyticsQ.data?.indicators ?? {}}
              requestedIndicators={selectedIndicators}
              indicatorsLoading={analyticsQ.isLoading || analyticsQ.isFetching}
              indicatorsError={analyticsQ.isError ? (analyticsQ.error instanceof Error ? analyticsQ.error.message : "analytics endpoint unreachable") : null}
              indicatorsProvenance={analyticsQ.data?.provenance ?? null}
              currency={q.currency ?? "USD"}
              onRetryIndicators={() => void analyticsQ.refetch()}
            />
          </Suspense>
        </div>
        <div className="term-panel min-w-0 p-4" aria-labelledby="brief-events">
          <h3 id="brief-events" className="term-label">Events timeline</h3>
          {analyticsQ.isLoading && (
            <div className="mt-2">
              <Skeleton label="loading events…" lines={3} />
            </div>
          )}
          {!analyticsQ.isLoading && events.length > 0 && (
            <ul className="mt-2 space-y-2 text-sm">
              {events.map((e, i) => (
                <li key={`${e.date}-${e.title}-${i}`} className="flex justify-between gap-2 border-b border-term-border pb-1">
                  <span className="min-w-0">{e.title}</span>
                  <span className="shrink-0 text-term-muted">{e.date || "—"}</span>
                </li>
              ))}
            </ul>
          )}
          {!analyticsQ.isLoading && events.length === 0 && (
            <p className="mt-2 text-xs text-term-muted" role="status">Events unavailable — no event feed for this symbol yet.</p>
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

function ForecastUnavailable({ detail, onRetry }) {
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

// Brief research preview: compact (A) deterministic summary + blended math
// note + AI-opinion teaser. Full A/B explanation lives on Forecast Details.
function ForecastCard({ price, currency, forecast: f, symbol }) {
  const why = (f.why ?? []).slice(0, 4);
  const risks = (f.risks ?? []).slice(0, 4);
  const whyText = why.length > 0 ? why.join(" + ") : "unavailable";
  const riskText = risks.length > 0 ? risks.join(" + ") : "unavailable";
  const quantProb = typeof f.quant_probability === "number" && Number.isFinite(f.quant_probability) ? f.quant_probability : f.probability;
  const versions = f.versions ?? {};
  return (
    <div className="mt-3 border-t border-term-border pt-3">
      <div className="flex flex-wrap items-center gap-2">
        <p className="term-label">Forecast · deterministic core (AI bounded, capped 20%)</p>
        <SourceBadge source="SOURCE: DETERMINISTIC" />
      </div>
      <div className="mt-1 space-y-1 text-sm">
        <p>
          Forecast: <b className="text-term-text">{f.label}, {f.horizon_days} days</b> <FreshnessBadge p={f.provenance} />
        </p>
        <p className="text-2xl font-bold text-term-green">
          Probability: {(f.probability * 100).toFixed(0)}% <ProvenanceBadge p={f.provenance} />
        </p>
        <p className="text-xs">Confidence: <b>{f.confidence}</b></p>
        <p className="text-xs">Data quality: <b className="text-term-cyan">{f.quality_grade}</b></p>
        <p className="text-xs">
          Model: <b>{versions.model_version ?? versions.model_name ?? f.provider}</b>
          <span className="text-term-muted"> · feature {versions.feature_version ?? "—"} · data {versions.data_version ?? "—"}</span>
        </p>
        <p className="text-xs">AI provider: <b>{f.provider && f.provider !== "deterministic-engine" ? f.provider : "none — deterministic core"}</b></p>
        <p className="text-xs text-term-muted">AI opinion: not requested on this page — bounded, capped 20%. Open Full Research for the (B) block.</p>
        <p className="text-xs">Why: <span className="text-term-muted">{whyText}</span></p>
        <p className="text-xs">Risks: <span className="text-term-muted">{riskText}</span></p>
      </div>
      <div className="mt-2">
        <BlendedForecastBar quantProb={quantProb} aiProb={f.ai_probability ?? null} aiWeight={f.ai_weight ?? 0} />
      </div>
      <dl className="mt-2 grid grid-cols-2 gap-2 text-xs md:grid-cols-4">
        <div>Last price: <b><CurrencyValue value={price} currency={currency} /></b></div>
        <div>Horizon: <b>{f.horizon_days}d</b></div>
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
            <ul className="list-disc pl-4 text-term-muted">{why.map((b, i) => <li key={`${b}-${i}`}>{b}</li>)}</ul>
          )}
        </div>
        <div className="rounded border border-term-border p-2">
          <p className="font-bold text-term-red">▼ BEAR — risks</p>
          {risks.length === 0 ? (
            <p className="text-term-muted">unavailable</p>
          ) : (
            <ul className="list-disc pl-4 text-term-muted">{risks.map((b, i) => <li key={`${b}-${i}`}>{b}</li>)}</ul>
          )}
        </div>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
        {symbol && (
          <Link to={`/forecast/${encodeURIComponent(symbol)}`} className="term-btn-ghost text-xs">
            FULL RESEARCH (A/B) →
          </Link>
        )}
        {symbol && (
          <a className="text-[11px] text-term-muted hover:text-term-text" href={auditForecastsUrl(symbol)} target="_blank" rel="noreferrer" title={`GET ${auditForecastsUrl(symbol)}`}>
            audit trail
          </a>
        )}
      </div>
      {/* Tier-gated stub — always unlocked, no enforcement. */}
      <div className="mt-2">
        <DeepResearchStub locked={false} tier="Free" feature="Deep Research" />
      </div>
      <p className="mt-2 text-[11px] text-term-muted">Not investment advice.</p>
    </div>
  );
}

function AnalyticsSnapshot({ analytics, loading, failed }) {
  return (
    <div className="term-panel min-w-0 p-4" aria-labelledby="brief-analytics">
      <h3 id="brief-analytics" className="term-label">Analytics snapshot · deterministic</h3>
      {analytics?.note && <p className="mt-1 text-[11px] text-term-amber" role="note">{analytics.note}</p>}
      {loading && <div className="mt-2"><Skeleton label="loading analytics…" lines={4} /></div>}
      {failed && <p className="mt-1 text-xs text-term-amber" role="alert">⚠ analytics endpoint unreachable — snapshot unavailable, rest of the page unaffected.</p>}
      {!loading && !failed && !analytics && <p className="mt-1 text-xs text-term-muted" role="status">No analytics payload yet.</p>}
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
          {["Technical", "Fundamentals", "Quality", "Valuation"].map((t) => (
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

function SnapshotCell({ title, data }) {
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
            <li key={k}>{k}: <b className="text-term-text">{typeof v === "object" ? JSON.stringify(v) : String(v)}</b></li>
          ))}
          {all.length > entries.length && <li className="text-[10px]" role="status">+{all.length - entries.length} more</li>}
        </ul>
      )}
    </div>
  );
}

function SecurityBriefError({ onRetry }) {
  return <ErrorState title="Security Brief failed" detail="Quote + forecast both unreachable." onRetry={onRetry} />;
}

function SecurityBriefLoading({ symbol }) {
  return (
    <div>
      <Loading label={`loading ${symbol}…`} />
      <p className="mt-2 text-[11px] text-term-muted">Not investment advice.</p>
    </div>
  );
}

export { SecurityBriefError, SecurityBriefLoading, SecurityBrief as default };
