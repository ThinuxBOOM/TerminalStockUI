import React, { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AI_PROFILES,
  FORECAST_HORIZONS,
  extractBackendDetail,
  friendlyAIError,
  getAnalytics,
  getForecast,
  postAIInsight,
  tryNormalizeAIOpinion,
} from "../../api/client";
import { getCalibrationHistory } from "../../api/calibrationHistory";
import { getRecentBacktests } from "../../api/backtestHistory";
import ProvenanceBadge from "../../components/ProvenanceBadge";
import FreshnessBadge from "../../components/FreshnessBadge";
import CalibrationChart from "../../components/CalibrationChart";
import { ResearchSection, AuditLink, DeepResearchStub } from "../../components/ResearchSection";
import Loading from "../../components/Loading";
import { formatDateTime, formatPct1 } from "../../utils/format";
import ErrorState from "../../components/ErrorState";

const DISCLOSURE = "Not investment advice. Forecasts are measurable probabilities from the deterministic engine; AI opinions are bounded and capped at 20% influence.";
const MAX_CAL_TABLE_ROWS = 20;
const MAX_CAL_TREND_ROWS = 10;
const MAX_ANALYTICS_CELLS = 12;

function ForecastDetails({ symbol }) {
  const [horizon, setHorizon] = useState(21);
  const [aiProfile, setAiProfile] = useState("Forecast Assist");
  const queryClient = useQueryClient();
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
  // Prefetch adjacent horizons in the background (warm cache, no waterfall).
  // Lifetime-linked: each prefetch is raced against unmount/horizon-change
  // via an AbortController, and rejections are swallowed so background
  // warmups never surface as unhandled rejections.
  useEffect(() => {
    const ctrl = new AbortController();
    for (const h of FORECAST_HORIZONS) {
      if (h === horizon) continue;
      if (ctrl.signal.aborted) break;
      void queryClient
        .prefetchQuery({
          queryKey: ["forecast", symbol, h],
          queryFn: ({ signal }) => {
            if (ctrl.signal.aborted) throw new Error("prefetch aborted");
            return getForecast(symbol, h, { signal });
          },
          staleTime: 300000,
        })
        .catch(() => {
          // background warmup only — never surface
        });
    }
    return () => {
      try {
        ctrl.abort();
      } catch {
        // never throws
      }
    };
  }, [queryClient, symbol, horizon]);
  const analyticsQ = useQuery({
    queryKey: ["analytics", symbol],
    queryFn: ({ signal }) => getAnalytics(symbol, { signal }),
    retry: false,
    staleTime: 300000,
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
    // Match the viewed horizon: a 5d run must not vouch for the 63d tab.
    () => getRecentBacktests().find(
      (r) => r.symbol === normalizedSymbol && (r.horizons.length === 0 || r.horizons.includes(horizon))
    ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [normalizedSymbol, horizon, forecastQ.dataUpdatedAt]
  );
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

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="term-label">Horizon (trading days)</span>
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
        <span className="text-term-muted">targets: direction probability · return range · vol regime · drawdown</span>
      </div>

      {forecastQ.isLoading && (
        <>
          <Loading label={`loading forecast ${symbol} ${horizon}d…`} />
          <p className="mt-1 text-[11px] text-term-muted" role="status">Cold fetch can take ~60s — warm cache makes repeats fast.</p>
        </>
      )}
      {forecastQ.isError && (
        <ErrorState
          title="Forecast unavailable"
          detail={`forecast endpoint unreachable (${extractBackendDetail(forecastQ.error, "unknown error")}) — forecast unavailable, no placeholder numbers shown`}
          onRetry={() => void forecastQ.refetch()}
        />
      )}
      {!forecastQ.isError && !forecastQ.isLoading && !live && (
        <p className="text-xs text-term-muted" role="status">
          Live forecast not yet returned — forecast unavailable, no placeholder numbers shown.
        </p>
      )}
      {!f && !forecastQ.isLoading && (
        <section className="term-panel p-4" role="status">
          <p className="term-label">Forecast · deterministic engine</p>
          <p className="mt-2 text-sm text-term-muted">
            Forecast unavailable for {symbol} at {horizon}d — the forecast endpoint is unreachable or returned no data. No placeholder numbers are shown.
          </p>
          <button className="term-btn-ghost mt-3 text-xs" type="button" onClick={() => void forecastQ.refetch()}>
            RETRY FORECAST
          </button>
        </section>
      )}

      {f && (
        <section className="term-panel p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-lg font-bold">
              Forecast: {f.label}, {f.horizon_days} days <FreshnessBadge p={f.provenance} />
            </h2>
            <AuditLink symbol={symbol} />
          </div>
          <p className="mt-1 text-display-sm term-num font-bold text-term-green">
            {formatPct1(f.probability)} <ProvenanceBadge p={f.provenance} />
          </p>
          <dl className="mt-2 grid grid-cols-2 gap-2 text-xs md:grid-cols-4">
            <div>Confidence: <b>{f.confidence}</b>{typeof f.confidence_score === "number" && Number.isFinite(f.confidence_score) ? <span className="text-term-muted"> ({f.confidence_score.toFixed(2)})</span> : null} <ProvenanceBadge p={f.provenance} /></div>
            <div>Data quality: <b className="text-term-cyan">{f.quality_grade}</b> <ProvenanceBadge p={f.provenance} /></div>
            <div>Provider: <b>{f.provider}</b></div>
            <div>Horizon: <b>{f.horizon_days}d</b></div>
            {typeof f.direction_probability_raw === "number" && Number.isFinite(f.direction_probability_raw) ? (
              <div>Raw prob: <b>{formatPct1(f.direction_probability_raw)}</b> <span className="text-term-muted">(calibrated {formatPct1(f.probability)})</span></div>
            ) : null}
            {typeof f.ensemble_spread === "number" && Number.isFinite(f.ensemble_spread) ? (
              <div>Spread: <b>{f.ensemble_spread.toFixed(2)}</b>{typeof f.n_members === "number" ? <span className="text-term-muted"> · {f.n_members} members</span> : null}</div>
            ) : null}
            {f.target_price && typeof f.target_price.last_close === "number" ? (
              <div>Target: <b>{Number.isFinite(Number(f.target_price.low)) ? Number(f.target_price.low).toFixed(2) : "—"} / {Number.isFinite(Number(f.target_price.mid)) ? Number(f.target_price.mid).toFixed(2) : "—"} / {Number.isFinite(Number(f.target_price.high)) ? Number(f.target_price.high).toFixed(2) : "—"}</b> <span className="text-term-muted">(last {f.target_price.last_close.toFixed(2)})</span></div>
            ) : null}
            {f.ensemble_weights && typeof f.ensemble_weights === "object" ? (
              <div className="col-span-2 md:col-span-4">Weights: <span className="text-term-muted">{Object.entries(f.ensemble_weights).map(([k, v]) => `${k}=${Number.isFinite(Number(v)) ? Number(v).toFixed(2) : "—"}`).join(" · ")}</span></div>
            ) : null}
          </dl>
          {(f.confidence_reasons ?? []).length > 0 ? (
            <p className="mt-1 text-[11px] text-term-muted">Confidence penalties: {(f.confidence_reasons ?? []).join("; ")}</p>
          ) : null}
          <div className="mt-2 grid gap-2 text-xs md:grid-cols-2">
            <div className="rounded border border-term-border p-2">
              <p className="font-bold text-term-green">Why (bullish drivers)</p>
              {(f.why ?? []).length === 0 ? (
                <p className="text-term-muted">unavailable</p>
              ) : (
                <ul className="list-disc pl-4 text-term-muted">{(f.why ?? []).map((w, i) => <li key={`${w}-${i}`}>{w}</li>)}</ul>
              )}
            </div>
            <div className="rounded border border-term-border p-2">
              <p className="font-bold text-term-red">Risks (bearish drivers)</p>
              {(f.risks ?? []).length === 0 ? (
                <p className="text-term-muted">unavailable</p>
              ) : (
                <ul className="list-disc pl-4 text-term-muted">{(f.risks ?? []).map((w, i) => <li key={`${w}-${i}`}>{w}</li>)}</ul>
              )}
            </div>
          </div>
        </section>
      )}

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
        <section className="term-panel p-4">
          <p className="term-label">Inputs · evidence · versions</p>
          <div className="mt-2 grid gap-2 text-xs md:grid-cols-3">
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
                <p className="mt-1 text-term-muted">
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

      {f && (
        <section className="term-panel p-4">
          <div className="mb-2 grid grid-cols-2 gap-2 text-xs md:grid-cols-5">
            <div className="rounded border border-term-border p-2">
              <p className="text-term-muted">Brier</p>
              <p className="text-base font-bold">{metaBrier === null || metaBrier === undefined ? "—" : Number(metaBrier).toFixed(4)}</p>
            </div>
            <div className="rounded border border-term-border p-2">
              <p className="text-term-muted">ECE</p>
              <p className="text-base font-bold">{metaEce === null || metaEce === undefined ? "—" : Number(metaEce).toFixed(4)}</p>
            </div>
            <div className="rounded border border-term-border p-2">
              <p className="text-term-muted">Windows</p>
              <p className="text-base font-bold">{metaN ?? "—"}</p>
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
              Brier/ECE above are from the persisted calibration snapshot{latestMeta.created_at ? ` (${new Date(latestMeta.created_at).toLocaleString()})` : ""}{latestMeta.model_version ? ` · model ${latestMeta.model_version}` : ""} — not computed from the live forecast above.
            </p>
          ) : (
            <p className="mt-1 text-[10px] text-term-muted">No persisted calibration snapshot yet — Brier/ECE unavailable.</p>
          )}
          <CalibrationChart rows={chartRows} title={`Calibration history · ${f?.horizon_days ?? horizon}d`} />
          {chartRows.length > 0 ? (
            <div className="mt-2 overflow-x-auto">
              {chartRows.length > MAX_CAL_TABLE_ROWS && (
                <p className="mb-1 text-[11px] text-term-muted" role="status">showing first {MAX_CAL_TABLE_ROWS} of {chartRows.length} bins.</p>
              )}
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
                  {chartRows.slice(0, MAX_CAL_TABLE_ROWS).map((r, i) => (
                    <tr key={`${r.bin_low}-${r.bin_high}-${i}`} className="border-t border-term-border">
                      <td className="py-1 pr-2">
                        {typeof r.bin_low === "number" && Number.isFinite(r.bin_low) ? r.bin_low.toFixed(2) : "—"}–
                        {typeof r.bin_high === "number" && Number.isFinite(r.bin_high) ? r.bin_high.toFixed(2) : "—"}
                      </td>
                      <td className="py-1 pr-2">{r.count}</td>
                      <td className="py-1 pr-2">{typeof r.mean_predicted === "number" && Number.isFinite(r.mean_predicted) ? r.mean_predicted.toFixed(3) : "—"}</td>
                      <td className="py-1 pr-2">{typeof r.fraction_positive === "number" && Number.isFinite(r.fraction_positive) ? r.fraction_positive.toFixed(3) : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {liveBins.length === 0 && latestMeta && (
                <p className="mt-1 text-[10px] text-term-muted">
                  Live bins unavailable — showing latest persisted snapshot{latestMeta.created_at ? ` (${latestMeta.created_at})` : ""}.
                </p>
              )}
            </div>
          ) : null}
          {!hasAnyCalibration && (
            <p className="mt-2 text-xs text-term-muted">No calibration bins yet — walk-forward history lands with the M3 engine; the Backtest Lab shows failures as well as successes.</p>
          )}
          {calHistoryQ.isLoading && <p className="mt-2 text-[11px] text-term-muted" role="status">loading calibration history…</p>}
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
                        <span>Brier {h.brier === null ? "—" : Number(h.brier).toFixed(4)} · ECE {h.ece === null ? "—" : Number(h.ece).toFixed(4)}{h.n_windows !== null ? ` · n=${h.n_windows}` : ""}</span>
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

      <section className="term-panel p-4">
        <p className="term-label">Deterministic analytics snapshot</p>
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
        {analyticsQ.isLoading && <p className="mt-1 text-xs text-term-muted">loading analytics…</p>}
        {analyticsQ.isError && (
          <p className="mt-1 text-xs text-term-amber">
            ⚠ analytics endpoint unreachable — snapshot unavailable, forecast above unaffected.{" "}
            <button className="term-btn-ghost ml-2 text-xs" type="button" onClick={() => void analyticsQ.refetch()}>
              RETRY ANALYTICS
            </button>
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
        {!analytics && !analyticsQ.isLoading && !analyticsQ.isError && <p className="mt-1 text-xs text-term-muted">No analytics payload yet.</p>}
      </section>

      <section className="term-panel p-4">
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

function AnalyticsGrid({ title, data }) {
  const entries = Object.entries(data ?? {});
  const visible = entries.slice(0, MAX_ANALYTICS_CELLS);
  return (
    <div className="mt-2">
      <p className="text-xs font-bold text-term-text">{title}</p>
      {entries.length === 0 ? (
        <p className="text-xs text-term-muted">unavailable</p>
      ) : (
        <>
          <dl className="mt-1 grid grid-cols-2 gap-1 text-xs md:grid-cols-4">
            {visible.map(([k, v]) => (
              <div key={k} className="rounded border border-term-border px-2 py-1">
                <dt className="text-term-muted">{k}</dt>
                <dd className="font-bold">{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd>
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
