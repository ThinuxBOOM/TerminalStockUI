import React, { Suspense, lazy, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { TIMEFRAME_PRESETS, SUPPORTED_INDICATORS, getAnalytics, getChart, getForecast, auditForecastsUrl, extractBackendDetail, loadFavoriteIndicators, resolveTimeframePreset, saveFavoriteIndicators } from "../../api/client";
import ProvenanceBadge from "../../components/ProvenanceBadge";
import FreshnessBadge from "../../components/FreshnessBadge";
import MarketStateBadge from "../../components/MarketStateBadge";
import StatusPill from "../../components/StatusPill";
import CurrencyValue from "../../components/CurrencyValue";
import { changeArrow, changeColor } from "../../utils/format";
import Loading from "../../components/Loading";
import Skeleton from "../../components/Skeleton";
import ErrorState from "../../components/ErrorState";
import { SourceBadge, BlendedForecastBar, DeepResearchStub } from "../../components/ResearchSection";

// PriceChart (and lightweight-charts) loads on demand — the brief header,
// forecast and events render without waiting for chart code.
const PriceChart = lazy(() => import("./PriceChart"));

const DISCLOSURE = "Not investment advice. For informational purposes only.";

function briefError(err, fallback) {
  return extractBackendDetail(err, fallback);
}

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
      <div className="mt-2 inline-flex rounded-md border border-term-border overflow-hidden" role="group" aria-label="Chart timeframe">
        {TIMEFRAME_PRESETS.map((p) => (
          <button
            key={p.id}
            type="button"
            onClick={() => onTimeframe(p.id)}
            aria-pressed={preset.id === p.id}
            title={`${p.id} — last ${p.limit} daily bars`}
            className={preset.id === p.id ? "px-3 py-1 text-2xs font-bold bg-term-green text-black" : "px-3 py-1 text-2xs font-bold text-term-muted hover:bg-term-panel2"}
          >
            {p.label}
          </button>
        ))}
      </div>
      <div className="mt-2">
        <p className="text-[10px] text-term-muted">Overlays — series from the analytics indicator API; legend toggles live on the chart</p>
        <div className="mt-1 flex flex-wrap gap-1.5">
          {SUPPORTED_INDICATORS.map((name) => {
            const active = selected.includes(name);
            return (
              <button
                key={name}
                type="button"
                aria-pressed={active}
                aria-label={`overlay ${name}`}
                onClick={() => onToggle(name)}
                className={active ? "rounded-full border border-term-green bg-term-greenDim px-2.5 py-0.5 text-2xs font-semibold text-term-green" : "rounded-full border border-term-border px-2.5 py-0.5 text-2xs text-term-muted hover:border-term-muted"}
              >
                {name}
              </button>
            );
          })}
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
  // V2 horizons: 1/7/14/21 trading days (was 5/21/63). Default 21 keeps
  // continuity; tabs let beginners compare tomorrow vs next month.
  const [forecastHorizon, setForecastHorizon] = useState(21);
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
  // Single-call chart: live quote + bars stitched with that exact quote
  // (GET /api/market_data/chart), so the header price and the chart's last
  // print are the same number from the same call — no quote-vs-bars skew.
  // retry: 1 — a cold first view warms the backend (live vendor fetch +
  // DB upsert); the retry then serves warm cache instead of surfacing
  // "Quote unavailable / timed out (cold start)". No coalesce for these
  // keys (client.js bypasses in-flight dedupe for chart/forecast), so the
  // retry is a genuine second request.
  const quote = useQuery({
    queryKey: ["chart", symbol, preset.timeframe, preset.limit],
    queryFn: ({ signal }) => getChart(symbol, preset.timeframe, preset.limit, { signal }),
    retry: 1,
    staleTime: 30000,
  });
  const forecastQ = useQuery({
    queryKey: ["forecast", symbol, forecastHorizon],
    queryFn: ({ signal }) => getForecast(symbol, forecastHorizon, { signal }),
    retry: 1,
    staleTime: 60000,
  });
  const analyticsQ = useQuery({
    queryKey: ["analytics", symbol, selectedIndicators.join(",")],
    queryFn: ({ signal }) => getAnalytics(symbol, { signal, indicators: selectedIndicators }),
    retry: false,
    staleTime: 60000,
  });
  const barsQ = quote; // alias: bars now ride the same chart response (candles/provenance below)
  const forecast = forecastQ.data ?? null;
  const analytics = analyticsQ.data ?? null;
  const events = useMemo(() => eventsFromAnalytics(analytics), [analytics]);
  const q = quote.data?.quote ?? null;
  // Non-blocking quote: the header skeletons inline while forecast, chart
  // and events (already fetching in parallel) render from their own queries.
  const quoteLoading = quote.isLoading && !q;
  if (quoteLoading) {
    return (
      <div className="max-w-full">
        <Skeleton label={`loading ${symbol}…`} lines={3} />
        {forecast && (
          <div className="term-panel mt-4 min-w-0 p-4">
            <ForecastCard price={null} currency="USD" forecast={forecast} symbol={symbol} horizon={forecastHorizon} onHorizon={setForecastHorizon} />
          </div>
        )}
        <div className="term-panel-hero mt-4 min-w-0 p-4">
          <h3 className="term-label">Price chart</h3>
          <Suspense fallback={<Skeleton label="loading chart..." lines={4} />}>
            <PriceChart
              symbol={symbol}
              data={barsQ.data?.candles ?? null}
              loading
              error={barsQ.isError ? briefError(barsQ.error, "bars endpoint unreachable") : null}
              provenance={barsQ.data?.provenance ?? null}
              indicators={analyticsQ.data?.indicators ?? {}}
              requestedIndicators={selectedIndicators}
              indicatorsLoading={analyticsQ.isLoading || analyticsQ.isFetching}
              indicatorsError={analyticsQ.isError ? briefError(analyticsQ.error, "analytics endpoint unreachable") : null}
              indicatorsProvenance={analyticsQ.data?.provenance ?? null}
              currency={null}
              onRetryIndicators={() => void analyticsQ.refetch()}
            />
          </Suspense>
        </div>
        <p className="mt-2 text-[11px] text-term-muted">{DISCLOSURE}</p>
      </div>
    );
  }
  if (quote.isError) {
    // Partial render: a quote/chart 502 must not discard already-fetched
    // forecast + analytics (parallel queries, already paid for). Show the
    // header ErrorState but still render ForecastCard/events/analytics below
    // when their queries resolved.
    const hasForecastFallback = forecastQ.data != null;
    const hasAnalyticsFallback = analyticsQ.data != null;
    if (!hasForecastFallback && !hasAnalyticsFallback) {
      return (
        <div className="max-w-full">
          <ErrorState
            title="Quote unavailable"
            detail={briefError(quote.error, "quote endpoint unreachable")}
            onRetry={() => {
              void quote.refetch();
              void forecastQ.refetch();
              void analyticsQ.refetch();
            }}
          />
          <p className="mt-2 text-[11px] text-term-muted">{DISCLOSURE}</p>
        </div>
      );
    }
    return (
      <div className="max-w-full">
        <ErrorState
          title="Quote unavailable"
          detail={briefError(quote.error, "quote endpoint unreachable")}
          onRetry={() => {
            void quote.refetch();
            void forecastQ.refetch();
            void analyticsQ.refetch();
          }}
        />
        {hasForecastFallback && (
          <div className="term-panel mt-4 min-w-0 p-4">
            <ForecastCard price={null} currency={null} forecast={forecastQ.data} symbol={symbol} horizon={forecastHorizon} onHorizon={setForecastHorizon} />
          </div>
        )}
        {hasAnalyticsFallback && (
          <section className="mt-4">
            <AnalyticsSnapshot analytics={analyticsQ.data} loading={false} failed={false} />
          </section>
        )}
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
            void analyticsQ.refetch();
          }}
        />
        <p className="mt-2 text-[11px] text-term-muted">{DISCLOSURE}</p>
      </div>
    );
  }
  return (
    <div className="max-w-full">
      <section className="term-panel-hero min-w-0 p-4" aria-labelledby="brief-forecast">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <h2 id="brief-forecast" className="font-sans text-2xs uppercase tracking-widest text-term-muted">
              {q.symbol}
            </h2>
            <p className="term-num text-display font-bold text-term-text">
              <CurrencyValue value={q.price} currency={q.currency ?? null} />
            </p>
            {typeof q.change_pct === "number" && Number.isFinite(q.change_pct) && (
              <p className={`term-num text-sm font-semibold ${changeColor(q.change_pct)}`}>
                {changeArrow(q.change_pct)} {q.change_pct >= 0 ? "+" : ""}{q.change_pct.toFixed(2)}%
              </p>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <StatusPill freshness={q.provenance} marketState={q.market_state} provenance={q.provenance} qualityGrade={q.provenance?.quality_grade ?? forecast?.quality_grade} size="lg" />
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
        {!forecastQ.isLoading && forecast && <ForecastCard price={q.price} currency={q.currency ?? null} forecast={forecast} symbol={symbol} horizon={forecastHorizon} onHorizon={setForecastHorizon} />}
        {!forecastQ.isLoading && !forecast && (
          <ForecastUnavailable detail="live forecast unreachable — no placeholder numbers shown" onRetry={() => void forecastQ.refetch()} />
        )}
      </section>
      <section className="mt-4 grid max-w-full gap-4 md:grid-cols-2">
        <div className="term-panel-hero min-w-0 p-4" aria-labelledby="brief-chart">
          <h3 id="brief-chart" className="term-label">Price chart</h3>
          <ChartControls preset={preset} onTimeframe={setTimeframeId} selected={selectedIndicators} onToggle={toggleIndicator} />
          <Suspense fallback={<Skeleton label="loading chart…" lines={4} />}>
            <PriceChart
              symbol={symbol}
              data={barsQ.data?.candles ?? null}
              loading={barsQ.isLoading || barsQ.isFetching}
              error={barsQ.isError ? briefError(barsQ.error, "bars endpoint unreachable") : null}
              provenance={barsQ.data?.provenance ?? null}
              indicators={analyticsQ.data?.indicators ?? {}}
              requestedIndicators={selectedIndicators}
              indicatorsLoading={analyticsQ.isLoading || analyticsQ.isFetching}
              indicatorsError={analyticsQ.isError ? briefError(analyticsQ.error, "analytics endpoint unreachable") : null}
              indicatorsProvenance={analyticsQ.data?.provenance ?? null}
              currency={q.currency ?? null}
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

// Brief research preview: V2 detailed drivers (up to 6 full-sentence
// bullets per side) + horizon tabs + plain-English summary for beginners.
function ForecastCard({ price, currency, forecast: f, symbol, horizon, onHorizon }) {
  const why = (f.why ?? []).slice(0, 6);
  const risks = (f.risks ?? []).slice(0, 6);
  const prob = Number(f.probability);
  const probPct = Number.isFinite(prob) ? `${(prob * 100).toFixed(0)}%` : "—";
  const plainSummary = (() => {
    if (!Number.isFinite(prob)) return "Not enough data to form a view right now.";
    if (prob >= 0.65) return `Leaning up — about a ${probPct} chance of rising over ${f.horizon_days}d. Good to research further, but never a guarantee.`;
    if (prob >= 0.55) return `Slightly positive — about a ${probPct} chance of rising over ${f.horizon_days}d. Worth watching.`;
    if (prob > 0.45) return `No clear direction — about ${probPct}. Waiting is a perfectly fine decision.`;
    if (prob > 0.35) return `Slightly negative — only about a ${probPct} chance of rising over ${f.horizon_days}d. Be extra careful.`;
    return `Leaning down — only about a ${probPct} chance of rising over ${f.horizon_days}d. Avoid chasing.`;
  })();
  const quantProb = typeof f.quant_probability === "number" && Number.isFinite(f.quant_probability) ? f.quant_probability : f.probability;
  const versions = f.versions ?? {};
  const horizons = [1, 7, 14, 21];
  return (
    <div className="mt-3 border-t border-term-border pt-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <p className="term-label">Forecast · math first (AI capped 20%)</p>
          <SourceBadge source="SOURCE: DETERMINISTIC" />
        </div>
        <div className="flex items-center gap-1" role="group" aria-label="Forecast horizon">
          {horizons.map((h) => (
            <button
              key={h}
              type="button"
              onClick={() => onHorizon && onHorizon(h)}
              aria-pressed={h === (horizon ?? f.horizon_days)}
              title={h === 1 ? "Tomorrow (1 trading day)" : `${h} trading days ahead`}
              className={h === (horizon ?? f.horizon_days) ? "term-btn px-2 py-0.5 text-[11px]" : "term-btn-ghost px-2 py-0.5 text-[11px]"}
            >
              {h}D
            </button>
          ))}
        </div>
      </div>
      <div className="mt-2 rounded bg-term-panel2 p-3 text-sm text-term-text" role="status">
        <b>In plain English:</b> {plainSummary}
      </div>
      <div className="mt-2 space-y-1 text-sm">
        <p>
          Forecast: <b className="text-term-text">{f.label}, {f.horizon_days} days</b> <FreshnessBadge p={f.provenance} />
        </p>
        <p className="text-2xl font-bold text-term-green">
          Chance of rising: {probPct} <ProvenanceBadge p={f.provenance} />
        </p>
        <p className="text-xs">Confidence: <b>{f.confidence}</b> <span className="text-term-muted">(how much the models agree)</span></p>
        <p className="text-xs">Data quality: <b className="text-term-cyan">{f.quality_grade}</b></p>
        <p className="text-xs">
          Model: <b>{versions.model_version ?? versions.model_name ?? f.provider}</b>
          <span className="text-term-muted"> · feature {versions.feature_version ?? "—"} · data {versions.data_version ?? "—"}</span>
        </p>
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
      <div className="mt-2 grid gap-2 text-sm md:grid-cols-2">
        <div className="term-panel-nested p-3">
          <p className="font-bold text-term-green">▲ Why it could go UP ({why.length})</p>
          <p className="mt-0.5 text-[11px] text-term-muted">Each point quotes a real number from the models — in full sentences.</p>
          {why.length === 0 ? (
            <p className="mt-1 text-term-muted">No upward drivers right now.</p>
          ) : (
            <ul className="mt-1 list-disc space-y-1 pl-4 text-term-text">{why.map((b, i) => <li key={`${b}-${i}`}>{b}</li>)}</ul>
          )}
        </div>
        <div className="term-panel-nested p-3">
          <p className="font-bold text-term-red">▼ Why it could go DOWN ({risks.length})</p>
          <p className="mt-0.5 text-[11px] text-term-muted">Risks and caution flags — read these before acting.</p>
          {risks.length === 0 ? (
            <p className="mt-1 text-term-muted">No major risks flagged.</p>
          ) : (
            <ul className="mt-1 list-disc space-y-1 pl-4 text-term-text">{risks.map((b, i) => <li key={`${b}-${i}`}>{b}</li>)}</ul>
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
      {analytics?.note && (
        <p
          className={analytics?.statements?.source
            ? "mt-1 text-[11px] text-term-green"
            : "mt-1 text-[11px] text-term-amber"}
          role="note"
        >
          {analytics.note}
        </p>
      )}
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
            <div key={t} className="term-panel-nested p-2">
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
    <div className="term-panel-nested min-w-0 p-2">
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
