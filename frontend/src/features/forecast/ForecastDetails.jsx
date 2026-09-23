import React, { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { keepPreviousData, useMutation, useQuery } from "@tanstack/react-query";
import {
  AI_PROFILES,
  FORECAST_HORIZONS,
  extractBackendDetail,
  friendlyAIError,
  getAnalytics,
  getChart,
  getForecast,
  postAIInsight,
  tryNormalizeAIOpinion,
} from "../../api/client";
import { getCalibrationHistory } from "../../api/calibrationHistory";
import { getBacktestHistory, getRecentBacktests } from "../../api/backtestHistory";
import { getSymbolNews } from "../../api/news";
import ProvenanceBadge from "../../components/ProvenanceBadge";
import FreshnessBadge from "../../components/FreshnessBadge";
import StatusPill from "../../components/StatusPill";
import CurrencyValue from "../../components/CurrencyValue";
import CalibrationChart from "../../components/CalibrationChart";
import CollapsibleSection from "../../components/CollapsibleSection";
import NewsPanel from "../../components/NewsPanel";
import Skeleton from "../../components/Skeleton";
import EmptyState from "../../components/EmptyState";
import { ResearchSection, AuditLink, DeepResearchStub, SourceBadge } from "../../components/ResearchSection";
import { formatDateTime, formatPct1 } from "../../utils/format";
import ErrorState from "../../components/ErrorState";

const DISCLOSURE = "Not investment advice. Forecasts are measurable probabilities from the deterministic engine; AI opinions are bounded and capped at 20% influence.";
const MAX_CAL_TABLE_ROWS = 20;
const MAX_CAL_TREND_ROWS = 10;
const MAX_ANALYTICS_CELLS = 12;
const MAX_NEWS_ITEMS = 6;

// Neutral direction word — only Rising / Falling / Neutral describe the signal.
function directionWord(probability, fallbackLabel, fallbackDirection) {
  const p = Number(probability);
  if (Number.isFinite(p)) {
    if (p >= 0.55) return "RISING";
    if (p <= 0.45) return "FALLING";
    return "NEUTRAL";
  }
  const hay = `${fallbackLabel ?? ""} ${fallbackDirection ?? ""}`.toLowerCase();
  if (/rising|up|bull/.test(hay)) return "RISING";
  if (/falling|down|bear/.test(hay)) return "FALLING";
  return "NEUTRAL";
}

function directionTone(word) {
  if (word === "RISING") return "text-term-green";
  if (word === "FALLING") return "text-term-red";
  return "text-term-muted";
}

function lastSuccessText(dataUpdatedAt) {
  if (!dataUpdatedAt) return "no successful fetch yet";
  try {
    const mins = Math.max(0, Math.round((Date.now() - dataUpdatedAt) / 60000));
    if (mins < 1) return "last success just now";
    if (mins < 60) return `last success ${mins}m ago`;
    return `last success ${formatDateTime(new Date(dataUpdatedAt))}`;
  } catch {
    return "last success unknown";
  }
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

function ForecastDetails({ symbol }) {
  const [horizon, setHorizon] = useState(21);
  const [aiProfile, setAiProfile] = useState("Forecast Assist");
  const forecastQ = useQuery({
    queryKey: ["forecast", symbol, horizon],
    queryFn: ({ signal }) => getForecast(symbol, horizon, { signal }),
    retry: false,
    // Backend caches forecasts 300s: warm tabs switch instantly.
    // keepPreviousData avoids flashing the skeleton on horizon switches.
    staleTime: 300000,
    gcTime: 600000,
    placeholderData: keepPreviousData,
  });
  // No adjacent-horizon prefetch: each horizon costs a full 500-bar
  // ensemble run server-side (2 sklearn fits). Horizon switches fetch
  // on demand and keepPreviousData covers the transition; the backend
  // 300s forecast cache makes repeats instant.
  const analyticsQ = useQuery({
    queryKey: ["analytics", symbol],
    queryFn: ({ signal }) => getAnalytics(symbol, { signal }),
    retry: false,
    staleTime: 300000,
    gcTime: 600000,
  });
  // Quote leg for the scroll progression Quote -> Forecast -> Analytics ->
  // Events -> Backtest. Single-call chart (live quote + bars stitched) so
  // the header price matches the last print — chart -> quote+bars fallback
  // lives in client.getChart and is preserved here.
  const quoteQ = useQuery({
    queryKey: ["chart", symbol, "1d", 30],
    queryFn: ({ signal }) => getChart(symbol, "1d", 30, { signal }),
    retry: 1,
    staleTime: 120000,
    gcTime: 600000,
  });
  const aiCtrl = useRef(null);
  useEffect(() => () => {
    try {
      aiCtrl.current?.abort();
    } catch {
      // never throws
    }
  }, []);
  const aiM = useMutation({
    mutationFn: () => {
      try {
        aiCtrl.current?.abort();
      } catch {
        // never throws
      }
      const ctrl = new AbortController();
      aiCtrl.current = ctrl;
      return postAIInsight(symbol, aiProfile, { signal: ctrl.signal });
    },
  });
  const live = forecastQ.data ?? null;
  const f = live;
  const analytics = analyticsQ.data ?? null;
  const calHistoryQ = useQuery({
    queryKey: ["calibration-history", symbol, horizon],
    queryFn: ({ signal }) => getCalibrationHistory(symbol, horizon, 20, { signal }),
    retry: false,
    staleTime: 300000,
    gcTime: 600000,
    placeholderData: keepPreviousData,
  });
  const calHistory = calHistoryQ.data ?? [];
  const latestMeta = calHistory[0] ?? null;
  const liveBins = f?.calibration ?? [];
  const chartRows = useMemo(
    () => (liveBins.length > 0 ? liveBins : (latestMeta?.reliability ?? [])),
    [liveBins, latestMeta]
  );
  const hasAnyCalibration = chartRows.length > 0 || calHistory.length > 0;
  const normalizedSymbol = String(symbol ?? "").trim().toUpperCase();
  const recentBacktest = useMemo(
    // Match the viewed horizon: a 1d run must not vouch for the 21d tab.
    () => getRecentBacktests().find(
      (r) => r.symbol === normalizedSymbol && (r.horizons.length === 0 || r.horizons.includes(horizon))
    ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [normalizedSymbol, horizon, forecastQ.dataUpdatedAt]
  );
  // Persisted backtest preview for the Backtest step of the hierarchy.
  // Preserves the history query chain (GET /api/backtest/:symbol).
  const backtestHistoryQ = useQuery({
    queryKey: ["backtest-history", normalizedSymbol],
    queryFn: ({ signal }) => getBacktestHistory(normalizedSymbol, false, { signal }),
    enabled: normalizedSymbol.length > 0,
    staleTime: 30000,
    retry: false,
  });
  const persistedRuns = useMemo(() => backtestHistoryQ.data ?? [], [backtestHistoryQ.data]);
  // Contextual news for the Events step — Headline/Source/Time, never
  // dominating market data. Preserves getSymbolNews + 423 copy in NewsPanel.
  const symbolNewsQ = useQuery({
    queryKey: ["symbol-news", normalizedSymbol],
    queryFn: ({ signal }) => getSymbolNews(normalizedSymbol, MAX_NEWS_ITEMS, { signal }),
    enabled: normalizedSymbol.length > 0,
    staleTime: 120000,
    retry: false,
  });
  const versions = f?.versions ?? {};
  const inputs = f?.inputs ?? {};
  const metaBrier = latestMeta?.brier ?? null;
  const metaEce = latestMeta?.ece ?? null;
  const metaN = latestMeta?.n_windows ?? (typeof inputs.n_windows === "number" ? inputs.n_windows : null);
  const metaModel =
    latestMeta?.model_version ??
    (typeof versions.model_version === "string" ? versions.model_version : null) ??
    (typeof inputs.model_version === "string" ? inputs.model_version : null);
  const metaData =
    latestMeta?.data_version ??
    (typeof versions.data_version === "string" ? versions.data_version : null) ??
    (typeof inputs.data_version === "string" ? inputs.data_version : null);
  // Safe degrade: a malformed AI payload never breaks the research section —
  // tryNormalize returns null and the (B) block renders its AI DISABLED state.
  // (No raw fallback: restoring the malformed object defeats the guard.)
  const aiOpinion = aiM.data ? tryNormalizeAIOpinion(aiM.data) : null;

  const quote = quoteQ.data?.quote ?? null;
  const quotePrice = typeof quote?.price === "number" && Number.isFinite(quote.price) ? quote.price : null;
  const quoteCurrency = quote?.currency ?? null;
  const instrument = quote?.instrument ?? null;
  const companyName = instrument?.company_name ?? null;
  const analyticsEvents = useMemo(() => eventsFromAnalytics(analytics), [analytics]);

  const dirWord = f ? directionWord(f.probability, f.label, f.direction) : "NEUTRAL";
  const headline = f ? `${f.horizon_days}D ${formatPct1(f.probability)} CHANCE OF ${dirWord}` : null;

  return (
    <div className="min-w-0 space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="term-label" id="horizon-label">Horizon (trading days)</span>
        <div className="flex flex-wrap gap-1.5" role="group" aria-labelledby="horizon-label">
          {FORECAST_HORIZONS.map((h) => (
            <button
              key={h}
              type="button"
              className={h === horizon ? "term-btn" : "term-btn-ghost"}
              aria-pressed={h === horizon}
              onClick={() => setHorizon(h)}
            >
              {h}d
            </button>
          ))}
        </div>
        <span className="text-term-muted">targets: direction probability · return range · vol regime · drawdown</span>
      </div>

      {/* Scroll progression anchor: Quote */}
      <section id="research-quote" aria-label="Quote" className="scroll-mt-4">
        <CollapsibleSection
          id="research-quote-h"
          title={`1 · Quote — ${normalizedSymbol}`}
          subtitle="Live price and company context. Forecast below never implies live when stale."
          defaultOpen
          badge={quote?.provenance ? <StatusPill provenance={quote.provenance} /> : null}
        >
          {quoteQ.isLoading && <Skeleton label={`loading quote ${symbol}…`} lines={3} variant="price" />}
          {quoteQ.isError && (
            <ErrorState
              title="Quote unavailable"
              detail={`provider didn't return fresh data (${extractBackendDetail(quoteQ.error, "quote endpoint unreachable")}), ${lastSuccessText(quoteQ.dataUpdatedAt)}`}
              onRetry={() => void quoteQ.refetch()}
            />
          )}
          {!quoteQ.isLoading && !quoteQ.isError && !quote && (
            <EmptyState
              title={`No quote for ${normalizedSymbol} yet`}
              detail="What: live price missing. Why: quote endpoint returned empty. Next: retry, or open the Security Brief for the chart fallback."
              actionLabel="Retry quote"
              onAction={() => void quoteQ.refetch()}
            />
          )}
          {quote && (
            <div className="min-w-0">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="term-num text-display-sm font-bold text-term-text">
                    <CurrencyValue value={quotePrice} currency={quoteCurrency} />
                  </p>
                  {companyName && <p className="mt-0.5 truncate text-sm text-term-muted">{companyName}</p>}
                  <p className="mt-0.5 text-[11px] text-term-muted">
                    {instrument?.exchange_mic ?? instrument?.exchange_symbol ?? ""}{" "}
                    {instrument?.sector ? `· ${instrument.sector}` : ""}{" "}
                    {instrument?.country ? `· ${instrument.country}` : ""}
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <StatusPill provenance={quote.provenance} marketState={quote.market_state} size="lg" />
                  <FreshnessBadge p={quote.provenance} />
                </div>
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                <ProvenanceBadge p={quote.provenance} />
              </div>
            </div>
          )}
        </CollapsibleSection>
      </section>

      {/* Hierarchy: Company -> Fundamentals -> Valuation -> Forecast */}
      <section id="research-company" aria-label="Company" className="scroll-mt-4">
        <CollapsibleSection
          id="research-company-h"
          title="2 · Company"
          subtitle="Who this research covers — deeper than the Security Brief overview."
          defaultOpen
          badge={<SourceBadge source="SOURCE: DETERMINISTIC" />}
        >
          {quoteQ.isLoading || analyticsQ.isLoading ? (
            <Skeleton label="loading company context…" lines={3} />
          ) : (
            <dl className="grid grid-cols-1 gap-2 text-xs sm:grid-cols-2 lg:grid-cols-4">
              {[
                ["Company", companyName ?? analytics?.company_name ?? "unavailable"],
                ["Symbol", quote?.symbol ?? normalizedSymbol],
                ["Exchange", instrument?.exchange_mic ?? instrument?.exchange_symbol ?? "unavailable"],
                ["Currency", quoteCurrency ?? "unavailable"],
                ["Sector", instrument?.sector ?? analytics?.fundamentals?.sector ?? "unavailable"],
                ["Country", instrument?.country ?? "unavailable"],
                ["Price", quotePrice !== null ? String(quotePrice) : "unavailable"],
                ["Market state", quote?.market_state ?? "unavailable"],
              ].map(([k, v]) => (
                <div key={k} className="term-panel-nested min-w-0 p-2">
                  <dt className="text-term-muted">{k}</dt>
                  <dd className="term-num mt-0.5 truncate font-bold text-term-text" title={String(v)}>{String(v)}</dd>
                </div>
              ))}
            </dl>
          )}
        </CollapsibleSection>
      </section>

      <div className="grid min-w-0 grid-cols-1 gap-4 lg:grid-cols-2">
        <section id="research-fundamentals" aria-label="Fundamentals" className="scroll-mt-4 min-w-0">
          <CollapsibleSection
            id="research-fundamentals-h"
            title="3 · Fundamentals"
            subtitle="Earnings, balance-sheet and quality signals from analytics."
            defaultOpen
            badge={<SourceBadge source="SOURCE: DETERMINISTIC" />}
          >
            <AnalyticsGrid title="Fundamentals" data={analytics?.fundamentals} emptyHint="Fundamentals unavailable for this symbol." />
            <AnalyticsGrid title="Quality" data={analytics?.quality} emptyHint="Quality signals unavailable." />
          </CollapsibleSection>
        </section>
        <section id="research-valuation" aria-label="Valuation" className="scroll-mt-4 min-w-0">
          <CollapsibleSection
            id="research-valuation-h"
            title="4 · Valuation"
            subtitle="Level and range context — intervals carry the range view."
            defaultOpen
            badge={<SourceBadge source="SOURCE: DETERMINISTIC" />}
          >
            <AnalyticsGrid title="Valuation" data={analytics?.valuation} emptyHint="Valuation snapshot unavailable." />
            {f?.target_price && typeof f.target_price.last_close === "number" ? (
              <p className="term-num mt-2 text-xs text-term-muted">
                Last close <CurrencyValue value={f.target_price.last_close} currency={quoteCurrency} /> · low{" "}
                <b className="text-term-text">{Number.isFinite(Number(f.target_price.low)) ? Number(f.target_price.low).toFixed(2) : "—"}</b> · mid{" "}
                <b className="text-term-text">{Number.isFinite(Number(f.target_price.mid)) ? Number(f.target_price.mid).toFixed(2) : "—"}</b> · high{" "}
                <b className="text-term-text">{Number.isFinite(Number(f.target_price.high)) ? Number(f.target_price.high).toFixed(2) : "—"}</b>
              </p>
            ) : (
              <p className="mt-2 text-xs text-term-muted">No target range returned — intervals below carry the range view.</p>
            )}
          </CollapsibleSection>
        </section>
      </div>

      {/* Scroll progression anchor: Forecast */}
      <section id="research-forecast" aria-label="Forecast" className="scroll-mt-4">
        <CollapsibleSection
          id="research-forecast-h"
          title={`5 · Forecast — ${horizon}d signal`}
          subtitle="Neutral Rising / Falling / Neutral language. Signal first, explanation after."
          defaultOpen
          badge={f?.provenance ? <StatusPill provenance={f.provenance} /> : <SourceBadge source="SOURCE: DETERMINISTIC" />}
        >
          {forecastQ.isLoading && (
            <div>
              <Skeleton label={`loading forecast ${symbol} ${horizon}d…`} lines={4} variant="price" />
              <p className="mt-1 text-[11px] text-term-muted" role="status">Cold fetch can take ~60s — warm cache makes repeats fast.</p>
            </div>
          )}
          {forecastQ.isError && (
            <ErrorState
              title="Forecast unavailable"
              detail={`provider didn't return fresh data (${extractBackendDetail(forecastQ.error, "forecast endpoint unreachable")}), ${lastSuccessText(forecastQ.dataUpdatedAt)} — no placeholder numbers shown`}
              onRetry={() => void forecastQ.refetch()}
            />
          )}
          {!forecastQ.isError && !forecastQ.isLoading && !live && (
            <EmptyState
              title={`No forecast for ${normalizedSymbol} at ${horizon}d yet`}
              detail="What: deterministic forecast missing. Why: endpoint unreachable or returned no data. Next: retry, or run a backtest to check skill first."
              actionLabel="Retry forecast"
              onAction={() => void forecastQ.refetch()}
            />
          )}

          {f && (
            <div className="min-w-0 space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h2 className={`term-num text-display-sm font-bold ${directionTone(dirWord)}`}>
                  {headline} <FreshnessBadge p={f.provenance} />
                </h2>
                <AuditLink symbol={symbol} />
              </div>
              <dl className="grid grid-cols-2 gap-2 text-xs md:grid-cols-4">
                <div className="term-panel-nested p-2">
                  <dt className="text-term-muted">Signal</dt>
                  <dd className={`term-num font-bold ${directionTone(dirWord)}`}>{dirWord}</dd>
                </div>
                <div className="term-panel-nested p-2">
                  <dt className="text-term-muted">Confidence</dt>
                  <dd className="term-num font-bold text-term-text">
                    {String(f.confidence ?? "Unknown").toUpperCase()}
                    {typeof f.confidence_score === "number" && Number.isFinite(f.confidence_score) ? <span className="text-term-muted"> ({f.confidence_score.toFixed(2)})</span> : null}
                  </dd>
                </div>
                <div className="term-panel-nested p-2">
                  <dt className="text-term-muted">Data Quality</dt>
                  <dd className="term-num font-bold text-term-cyan">{f.quality_grade ?? "U"}</dd>
                </div>
                <div className="term-panel-nested p-2">
                  <dt className="text-term-muted">Forecast</dt>
                  <dd className="term-num font-bold text-term-text">{formatPct1(f.probability)}</dd>
                </div>
                <div className="term-panel-nested p-2">
                  <dt className="text-term-muted">Provider</dt>
                  <dd className="font-bold text-term-text">{f.provider}</dd>
                </div>
                <div className="term-panel-nested p-2">
                  <dt className="text-term-muted">Horizon</dt>
                  <dd className="term-num font-bold text-term-text">{f.horizon_days}d</dd>
                </div>
                {typeof f.direction_probability_raw === "number" && Number.isFinite(f.direction_probability_raw) ? (
                  <div className="term-panel-nested p-2">
                    <dt className="text-term-muted">Raw prob</dt>
                    <dd className="term-num font-bold text-term-text">{formatPct1(f.direction_probability_raw)} <span className="font-normal text-term-muted">(calibrated {formatPct1(f.probability)})</span></dd>
                  </div>
                ) : null}
                {typeof f.ensemble_spread === "number" && Number.isFinite(f.ensemble_spread) ? (
                  <div className="term-panel-nested p-2">
                    <dt className="text-term-muted">Spread</dt>
                    <dd className="term-num font-bold text-term-text">{f.ensemble_spread.toFixed(2)}{typeof f.n_members === "number" ? <span className="font-normal text-term-muted"> · {f.n_members} members</span> : null}</dd>
                  </div>
                ) : null}
              </dl>
              {f.ensemble_weights && typeof f.ensemble_weights === "object" ? (
                <p className="text-xs text-term-muted">Weights: <span className="term-num">{Object.entries(f.ensemble_weights).map(([k, v]) => `${k}=${Number.isFinite(Number(v)) ? Number(v).toFixed(2) : "—"}`).join(" · ")}</span></p>
              ) : null}
              {(f.calibration_method || f.adaptive_weights !== null || typeof f.skill_brier === "number" || typeof f.skill_ece === "number") ? (
                <p className="text-xs text-term-muted">
                  Calibration: <b className="text-term-text">{f.calibration_method ?? "shrinkage-0.8"}</b>
                  {typeof f.adaptive_weights === "boolean" ? <span> · {f.adaptive_weights ? "adaptive weights" : "fixed weights"}</span> : null}
                  {typeof f.skill_brier === "number" && Number.isFinite(f.skill_brier) ? <span className="term-num"> · skill Brier {f.skill_brier.toFixed(4)}</span> : null}
                  {typeof f.skill_ece === "number" && Number.isFinite(f.skill_ece) ? <span className="term-num"> · ECE {f.skill_ece.toFixed(4)}</span> : null}
                </p>
              ) : null}
              {(f.confidence_reasons ?? []).length > 0 ? (
                <p className="text-[11px] text-term-muted">Confidence notes: {(f.confidence_reasons ?? []).join("; ")}</p>
              ) : null}
              <div className="grid gap-2 text-xs md:grid-cols-2">
                <div className="rounded border border-term-border p-2">
                  <p className="font-bold text-term-green">Why the signal leans this way</p>
                  {(f.why ?? []).length === 0 ? (
                    <p className="text-term-muted">unavailable</p>
                  ) : (
                    <ul className="list-disc pl-4 text-term-muted">{(f.why ?? []).map((w, i) => <li key={`${w}-${i}`}>{w}</li>)}</ul>
                  )}
                </div>
                <div className="rounded border border-term-border p-2">
                  <p className="font-bold text-term-red">What could go the other way</p>
                  {(f.risks ?? []).length === 0 ? (
                    <p className="text-term-muted">unavailable</p>
                  ) : (
                    <ul className="list-disc pl-4 text-term-muted">{(f.risks ?? []).map((w, i) => <li key={`${w}-${i}`}>{w}</li>)}</ul>
                  )}
                </div>
              </div>
              <p className="text-[11px] text-term-muted" role="note">
                Signal wording is neutral (Rising / Falling / Neutral) with Confidence and Data Quality shown first — explanation follows.
              </p>
            </div>
          )}
        </CollapsibleSection>
      </section>

      {/* Research/Explanation: (A) deterministic engine + blended math + (B) AI opinion */}
      {f && (
        <ResearchSection
          symbol={symbol}
          forecast={f}
          aiOpinion={aiOpinion}
          aiWeight={f.ai_weight ?? 0}
          calibrationHistory={calHistory}
          disclosure={f.disclosure ?? DISCLOSURE}
          onRequestAI={aiM.data ? undefined : () => aiM.mutate()}
          requestingAI={aiM.isPending}
          aiRequestError={aiM.isError ? friendlyAIError(aiM.error) : null}
        />
      )}

      {f && (
        <section className="term-panel min-w-0 p-4" aria-label="Inputs, evidence and versions">
          <p className="term-label">Inputs · evidence · versions</p>
          <div className="mt-2 grid grid-cols-1 gap-2 text-xs md:grid-cols-3">
            <div className="rounded border border-term-border p-2">
              <p className="font-bold">Model versions</p>
              <ul className="mt-1 space-y-0.5 text-term-muted">
                {f.versions ? (
                  Object.entries(f.versions).map(([k, v]) => <li key={k}>{k}: <b className="text-term-text">{String(v)}</b></li>)
                ) : (
                  <li>unavailable</li>
                )}
              </ul>
            </div>
            <div className="rounded border border-term-border p-2">
              <p className="font-bold">Evidence IDs</p>
              {(f.evidence_ids ?? []).length === 0 ? (
                <p className="mt-1 text-term-muted">none</p>
              ) : (
                <>
                  <p className="mt-1">
                    {(f.evidence_ids ?? []).slice(0, 24).map((e, i) => (
                      <code key={`${e}-${i}`} className="mr-1 rounded bg-term-bg px-1 py-0.5 text-term-cyan">{e}</code>
                    ))}
                    {(f.evidence_ids ?? []).length > 24 && (
                      <span className="text-term-muted">… +{(f.evidence_ids ?? []).length - 24} more</span>
                    )}
                  </p>
                  {f.inputs && (
                    <ul className="mt-2 space-y-0.5 text-term-muted">
                      {Object.entries(f.inputs).slice(0, 12).map(([k, v]) => (
                        <li key={k}>{k}: <span className="text-term-text">{String(v)}</span></li>
                      ))}
                    </ul>
                  )}
                </>
              )}
            </div>
            <div className="rounded border border-term-border p-2">
              <p className="font-bold">Intervals (expected return)</p>
              {f.intervals && typeof f.intervals.low === "number" && typeof f.intervals.mid === "number" && typeof f.intervals.high === "number" &&
              Number.isFinite(f.intervals.low) && Number.isFinite(f.intervals.mid) && Number.isFinite(f.intervals.high) ? (
                <p className="term-num mt-1 text-term-muted">
                  low <b className="text-term-red">{(f.intervals.low * 100).toFixed(1)}%</b> · mid{" "}
                  <b className="text-term-text">{(f.intervals.mid * 100).toFixed(1)}%</b> · high{" "}
                  <b className="text-term-green">{(f.intervals.high * 100).toFixed(1)}%</b> <ProvenanceBadge p={f.provenance} />
                </p>
              ) : (
                <p className="mt-1 text-term-muted">unavailable</p>
              )}
            </div>
          </div>
          <div className="mt-2">
            <ProvenanceBadge p={f?.provenance} />
          </div>
        </section>
      )}

      {/* Scroll progression anchor: Analytics (Technical deep dive) */}
      <section id="research-analytics" aria-label="Analytics" className="scroll-mt-4">
        <CollapsibleSection
          id="research-technical-h"
          title="6 · Technical"
          subtitle="Deterministic analytics snapshot — momentum, trend and volatility."
          defaultOpen={false}
          badge={analytics?.provenance ? <StatusPill provenance={analytics.provenance} /> : null}
        >
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
          {analyticsQ.isLoading && <div className="mt-2"><Skeleton label="loading analytics…" lines={4} variant="table" /></div>}
          {analyticsQ.isError && (
            <ErrorState
              title="Analytics unavailable"
              detail={`provider didn't return fresh data (${extractBackendDetail(analyticsQ.error, "analytics endpoint unreachable")}), ${lastSuccessText(analyticsQ.dataUpdatedAt)} — forecast above unaffected`}
              onRetry={() => void analyticsQ.refetch()}
            />
          )}
          {analytics && (
            <>
              <AnalyticsGrid title="Technical" data={analytics.technical} />
              <div className="mt-2 flex flex-wrap gap-2">
                <ProvenanceBadge p={analytics.provenance} />
                <FreshnessBadge p={analytics.provenance} />
              </div>
            </>
          )}
          {!analytics && !analyticsQ.isLoading && !analyticsQ.isError && (
            <EmptyState
              title="No analytics payload yet"
              detail="What: technical snapshot missing. Why: analytics endpoint returned empty. Next: retry analytics."
              actionLabel="Retry analytics"
              onAction={() => void analyticsQ.refetch()}
            />
          )}
        </CollapsibleSection>
      </section>

      {f && (
        <section className="term-panel min-w-0 p-4" aria-label="Calibration history">
          <p className="term-label">Calibration history · Brier / ECE</p>
          <div className="mb-2 mt-2 grid grid-cols-2 gap-2 text-xs md:grid-cols-5">
            <div className="rounded border border-term-border p-2">
              <p className="text-term-muted">Brier</p>
              <p className="term-num text-base font-bold">{metaBrier === null || metaBrier === undefined ? "—" : Number(metaBrier).toFixed(4)}</p>
            </div>
            <div className="rounded border border-term-border p-2">
              <p className="text-term-muted">ECE</p>
              <p className="term-num text-base font-bold">{metaEce === null || metaEce === undefined ? "—" : Number(metaEce).toFixed(4)}</p>
            </div>
            <div className="rounded border border-term-border p-2">
              <p className="text-term-muted">Windows</p>
              <p className="term-num text-base font-bold">{metaN ?? "—"}</p>
            </div>
            <div className="rounded border border-term-border p-2">
              <p className="text-term-muted">Model</p>
              <p className="truncate text-xs font-bold" title={String(metaModel ?? "")}>{metaModel ? String(metaModel) : "—"}</p>
            </div>
            <div className="rounded border border-term-border p-2">
              <p className="text-term-muted">Data</p>
              <p className="truncate text-xs font-bold" title={String(metaData ?? "")}>{metaData ? String(metaData) : "—"}</p>
            </div>
          </div>
          {latestMeta ? (
            <p className="mt-1 text-[10px] text-term-muted">
              Brier/ECE above are from the persisted calibration snapshot{latestMeta.created_at ? ` (${formatDateTime(latestMeta.created_at)})` : ""}{latestMeta.model_version ? ` · model ${latestMeta.model_version}` : ""}{latestMeta.calibration_method ? ` · ${latestMeta.calibration_method}` : ""}{typeof latestMeta.calibrated_brier === "number" && Number.isFinite(latestMeta.calibrated_brier) ? ` · calibrated Brier ${Number(latestMeta.calibrated_brier).toFixed(4)}` : ""}{typeof latestMeta.calibrated_ece === "number" && Number.isFinite(latestMeta.calibrated_ece) ? ` · ECE ${Number(latestMeta.calibrated_ece).toFixed(4)}` : ""} — not computed from the live forecast above.
            </p>
          ) : (
            <p className="mt-1 text-[10px] text-term-muted">No persisted calibration snapshot yet — Brier/ECE unavailable.</p>
          )}
          {calHistoryQ.isLoading ? (
            <div className="mt-2"><Skeleton label="loading calibration history…" lines={3} variant="chart" /></div>
          ) : (
            <CalibrationChart rows={chartRows} title={`Calibration history · ${f?.horizon_days ?? horizon}d`} />
          )}
          {chartRows.length > 0 ? (
            <div className="mt-2 overflow-x-auto">
              {chartRows.length > MAX_CAL_TABLE_ROWS && (
                <p className="mb-1 text-[11px] text-term-muted" role="status">showing first {MAX_CAL_TABLE_ROWS} of {chartRows.length} bins.</p>
              )}
              <table className="w-full min-w-[480px] text-xs">
                <caption className="sr-only">Reliability table: predicted vs observed per bin</caption>
                <thead className="sticky top-0 z-10 bg-term-panel">
                  <tr className="text-left text-term-muted">
                    <th scope="col" className="py-1 pr-2">Bin</th>
                    <th scope="col" className="py-1 pr-2 text-right">n</th>
                    <th scope="col" className="py-1 pr-2 text-right">Mean predicted</th>
                    <th scope="col" className="py-1 pr-2 text-right">Fraction positive</th>
                  </tr>
                </thead>
                <tbody>
                  {chartRows.slice(0, MAX_CAL_TABLE_ROWS).map((r, i) => (
                    <tr key={`${r.bin_low}-${r.bin_high}-${i}`} className="border-t border-term-border">
                      <td className="term-num py-1 pr-2">
                        {typeof r.bin_low === "number" && Number.isFinite(r.bin_low) ? r.bin_low.toFixed(2) : "—"}–
                        {typeof r.bin_high === "number" && Number.isFinite(r.bin_high) ? r.bin_high.toFixed(2) : "—"}
                      </td>
                      <td className="term-num py-1 pr-2 text-right">{r.count}</td>
                      <td className="term-num py-1 pr-2 text-right">{typeof r.mean_predicted === "number" && Number.isFinite(r.mean_predicted) ? r.mean_predicted.toFixed(3) : "—"}</td>
                      <td className="term-num py-1 pr-2 text-right">{typeof r.fraction_positive === "number" && Number.isFinite(r.fraction_positive) ? r.fraction_positive.toFixed(3) : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {liveBins.length === 0 && latestMeta && (
                <p className="mt-1 text-[10px] text-term-muted">
                  Live bins unavailable — showing latest persisted snapshot{latestMeta.created_at ? ` (${formatDateTime(latestMeta.created_at)})` : ""}.
                </p>
              )}
            </div>
          ) : null}
          {!hasAnyCalibration && !calHistoryQ.isLoading && (
            <EmptyState
              title="No calibration bins yet"
              detail="What: reliability history missing. Why: walk-forward history hasn't landed for this horizon. Next: run the Backtest Lab — failures show here too."
              actionLabel="Open Backtest Lab"
            />
          )}
          {calHistoryQ.isError && (
            <p className="mt-2 text-[11px] text-term-amber" role="alert">
              ⚠ calibration history unavailable ({extractBackendDetail(calHistoryQ.error, "backend unreachable")}) — live bins above unaffected.
            </p>
          )}
          {calHistory.length > 0 && (
            <div className="mt-3">
              <p className="term-label">Calibration trend · past snapshots</p>
              {calHistory.length > MAX_CAL_TREND_ROWS && (
                <p className="mt-1 text-[11px] text-term-muted" role="status">showing first {MAX_CAL_TREND_ROWS} of {calHistory.length} snapshots.</p>
              )}
              <div className="mt-1 space-y-1">
                {calHistory.slice(0, MAX_CAL_TREND_ROWS).map((h, i) => {
                  const b = h.brier ?? 0;
                  const e = h.ece ?? 0;
                  const bWidth = Math.min(100, Math.max(0, (b / 0.25) * 100));
                  const eWidth = Math.min(100, Math.max(0, (e / 0.2) * 100));
                  return (
                    <div key={`${h.created_at ?? "snapshot"}-${i}`} className="text-[11px]">
                      <div className="flex justify-between gap-2 text-term-muted">
                        <span>{h.created_at ? formatDateTime(h.created_at) : `snapshot ${i + 1}`}</span>
                        <span className="term-num">Brier {h.brier === null ? "—" : Number(h.brier).toFixed(4)} · ECE {h.ece === null ? "—" : Number(h.ece).toFixed(4)}{h.n_windows !== null ? ` · n=${h.n_windows}` : ""}{typeof h.calibrated_brier === "number" && Number.isFinite(h.calibrated_brier) ? ` · cal ${Number(h.calibrated_brier).toFixed(4)}` : ""}{h.calibration_method ? ` · ${h.calibration_method}` : ""}</span>
                      </div>
                      <div className="mt-0.5 h-1 w-full rounded bg-term-border">
                        <div className="h-1 rounded bg-term-green" style={{ width: `${bWidth}%` }} title={`Brier ${h.brier ?? "—"} (0 = perfect, 0.25 = coin-flip)`} />
                      </div>
                      <div className="mt-0.5 h-1 w-full rounded bg-term-border">
                        <div className="h-1 rounded bg-term-cyan" style={{ width: `${eWidth}%` }} title={`ECE ${h.ece ?? "—"} (lower = better calibrated)`} />
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
            <Link className="term-btn-ghost text-xs" to={`/backtest?symbol=${encodeURIComponent(symbol)}`}>
              RUN BACKTEST →
            </Link>
            {recentBacktest && (
              <span className="text-[11px] text-term-muted">
                Backtest run {formatDateTime(recentBacktest.at)}{recentBacktest.horizons.length > 0 ? ` (${recentBacktest.horizons.map((x) => `${x}d`).join(", ")})` : ""}— see the Backtest Lab for details.
              </span>
            )}
          </div>
        </section>
      )}

      {/* Scroll progression anchor: Events */}
      <section id="research-events" aria-label="Events" className="scroll-mt-4">
        <CollapsibleSection
          id="research-events-h"
          title="7 · Events"
          subtitle="Contextual headlines for this symbol — never dominating market data."
          defaultOpen
          badge={<SourceBadge source="SOURCE: DETERMINISTIC" />}
        >
          {analyticsEvents.length > 0 && (
            <div className="mb-3">
              <p className="term-label">Analytics events</p>
              <ul className="mt-1 space-y-1 text-xs">
                {analyticsEvents.map((e, i) => (
                  <li key={`${e.date}-${e.title}-${i}`} className="flex justify-between gap-2 border-b border-term-border pb-1">
                    <span className="min-w-0 truncate">{e.title}</span>
                    <span className="term-num shrink-0 text-term-muted">{e.date || "—"}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <div aria-label={`News for ${normalizedSymbol}`}>
            <NewsPanel
              data={symbolNewsQ.data ?? null}
              isLoading={symbolNewsQ.isLoading}
              isError={symbolNewsQ.isError}
              error={symbolNewsQ.error}
              onRetry={() => void symbolNewsQ.refetch()}
            />
          </div>
          {analytics?.provenance && (
            <div className="mt-2 flex flex-wrap gap-2">
              <ProvenanceBadge p={analytics.provenance} />
            </div>
          )}
        </CollapsibleSection>
      </section>

      {/* Scroll progression anchor: Backtest */}
      <section id="research-backtest" aria-label="Backtest" className="scroll-mt-4">
        <CollapsibleSection
          id="research-backtest-h"
          title="8 · Backtest"
          subtitle="Does this signal hold up out-of-sample? Failures included."
          defaultOpen={false}
          badge={<SourceBadge source="SOURCE: DETERMINISTIC" />}
        >
          {backtestHistoryQ.isLoading && <Skeleton label="loading backtest history…" lines={3} variant="table" />}
          {backtestHistoryQ.isError && (
            <p className="text-xs text-term-amber" role="alert">
              ⚠ persisted backtest history unavailable ({extractBackendDetail(backtestHistoryQ.error, "backend unreachable")}) — run the lab for a fresh scorecard.
            </p>
          )}
          {!backtestHistoryQ.isLoading && !backtestHistoryQ.isError && persistedRuns.length === 0 && (
            <EmptyState
              title={`No persisted backtests for ${normalizedSymbol} yet`}
              detail="What: no scored windows stored. Why: no lab run has completed for this symbol. Next: run a backtest — Brier/ECE and the reliability table land here."
              actionLabel="Open Backtest Lab"
            />
          )}
          {persistedRuns.length > 0 && (
            <ul className="space-y-1 text-xs">
              {persistedRuns.slice(0, 5).map((run, i) => {
                const keys = Object.keys(run.metrics ?? {}).sort((a, b) => Number(a) - Number(b));
                const first = keys.length > 0 ? run.metrics[keys[0]] : undefined;
                return (
                  <li key={run.run_id ?? `run-${i}`} className="flex flex-wrap items-center justify-between gap-2 border-b border-term-border pb-1">
                    <span className="term-num text-term-muted">{String(run.run_id ?? "").slice(0, 8)} · {run.as_of ? formatDateTime(run.as_of) : "—"}</span>
                    <span className="term-num">Brier {first?.brier === null || first?.brier === undefined ? "—" : Number(first.brier).toFixed(4)} · ECE {first?.ece === null || first?.ece === undefined ? "—" : Number(first.ece).toFixed(4)}</span>
                  </li>
                );
              })}
            </ul>
          )}
          {recentBacktest && (
            <p className="mt-2 text-[11px] text-term-muted">
              Recent in this browser: {recentBacktest.symbol} {recentBacktest.horizons.map((x) => `${x}d`).join(", ")} · {formatDateTime(recentBacktest.at)}
            </p>
          )}
          <div className="mt-2 flex flex-wrap gap-2">
            <Link className="term-btn text-xs" to={`/backtest?symbol=${encodeURIComponent(symbol)}`}>
              OPEN BACKTEST LAB →
            </Link>
            <Link className="term-btn-ghost text-xs" to={`/backtest?symbol=${encodeURIComponent(symbol)}`}>
              RE-RUN {horizon}D
            </Link>
          </div>
        </CollapsibleSection>
      </section>

      <section className="term-panel min-w-0 p-4" aria-label="Limitations">
        <p className="term-label">Limitations</p>
        {f && (f.limitations ?? []).length > 0 ? (
          <ul className="mt-1 list-disc pl-5 text-sm text-term-muted">{(f.limitations ?? []).map((l, i) => <li key={`${l}-${i}`}>{l}</li>)}</ul>
        ) : (
          <p className="mt-1 text-sm text-term-muted">unavailable — live forecast not loaded.</p>
        )}
        <div className="mt-3 rounded border border-term-amber bg-term-panel p-3 text-xs text-term-amber">
          {f?.disclosure ?? DISCLOSURE}
        </div>
      </section>

      <div className="flex flex-wrap items-center gap-2 text-xs">
        <label className="term-label" htmlFor="ai-profile">AI profile</label>
        <select id="ai-profile" className="term-input" value={aiProfile} onChange={(e) => setAiProfile(e.target.value)}>
          {AI_PROFILES.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
        <button className="term-btn" type="button" disabled={aiM.isPending} onClick={() => aiM.mutate()}>
          {aiM.isPending ? "REQUESTING…" : aiM.data ? "REFRESH AI OPINION" : "REQUEST AI OPINION"}
        </button>
        {aiM.isPending && (
          <button
            className="term-btn-ghost text-xs"
            type="button"
            onClick={() => {
              try {
                aiCtrl.current?.abort();
              } catch {
                // never throws
              }
              aiM.reset();
            }}
          >
            CANCEL
          </button>
        )}
        {aiM.isError && <span className="text-term-amber">⚠ {friendlyAIError(aiM.error)}— deterministic forecast above is unaffected.</span>}
      </div>
      {/* Tier-gated stub — always unlocked, no enforcement. */}
      <DeepResearchStub locked={false} tier="Free" feature="Deep Research" />
      <p className="text-[11px] text-term-muted">{f?.disclosure ?? DISCLOSURE}</p>
    </div>
  );
}

function AnalyticsGrid({ title, data, emptyHint }) {
  const entries = Object.entries(data ?? {});
  const visible = entries.slice(0, MAX_ANALYTICS_CELLS);
  return (
    <div className="mt-2 min-w-0">
      <p className="text-xs font-bold text-term-text">{title}</p>
      {entries.length === 0 ? (
        <p className="text-xs text-term-muted">{emptyHint ?? "unavailable"}</p>
      ) : (
        <>
          <dl className="mt-1 grid grid-cols-1 gap-1 text-xs sm:grid-cols-2 xl:grid-cols-3">
            {visible.map(([k, v]) => (
              <div key={k} className="min-w-0 rounded border border-term-border px-2 py-1">
                <dt className="truncate text-term-muted" title={k}>{k}</dt>
                <dd className="term-num break-words font-bold text-term-text">{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd>
              </div>
            ))}
          </dl>
          {entries.length > visible.length && (
            <p className="mt-1 text-[10px] text-term-muted" role="status">showing first {visible.length} of {entries.length} — +{entries.length - visible.length} more.</p>
          )}
        </>
      )}
    </div>
  );
}

export { ForecastDetails as default };
