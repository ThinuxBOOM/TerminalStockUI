import React, { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { FORECAST_HORIZONS, auditForecastsUrl, extractBackendDetail, runBacktest } from "../api/client";
import { getBacktestHistory, getRecentBacktests, saveRecentBacktest } from "../api/backtestHistory";
import { formatDateTime } from "../utils/format";
import useWatchlist from "../hooks/useWatchlist";
import ProvenanceBadge from "../components/ProvenanceBadge";
import FreshnessBadge from "../components/FreshnessBadge";
import StatusPill from "../components/StatusPill";
import CalibrationChart from "../components/CalibrationChart";
import { SourceBadge } from "../components/ResearchSection";
import Skeleton from "../components/Skeleton";
import EmptyState from "../components/EmptyState";
import ErrorState from "../components/ErrorState";

const MAX_HISTORY_ROWS = 20;
const MAX_RELIABILITY_ROWS = 20;

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

function BacktestLabPage() {
  const [searchParams] = useSearchParams();
  const [symbol, setSymbol] = useState(
    () => searchParams.get("symbol")?.trim().toUpperCase() || "AAPL"
  );
  const [horizons, setHorizons] = useState([21]);
  const [formError, setFormError] = useState(null);
  const { add: addToWatchlist } = useWatchlist();
  useEffect(() => {
    const s = searchParams.get("symbol")?.trim().toUpperCase();
    if (s) setSymbol(s);
  }, [searchParams]);
  const lab = useMutation({
    mutationFn: ({ s, h }) => runBacktest(s, h),
    onSuccess: (_data, vars) => {
      saveRecentBacktest(vars.s, vars.h);
    },
  });
  const trimmedSymbol = String(symbol ?? "").trim().toUpperCase();
  // History follows a debounced symbol: typing "AAPL" must not fire
  // four sequential history fetches.
  const [historySymbol, setHistorySymbol] = useState(trimmedSymbol);
  useEffect(() => {
    const t = setTimeout(() => setHistorySymbol(String(symbol ?? "").trim().toUpperCase()), 500);
    return () => clearTimeout(t);
  }, [symbol]);
  const historyQ = useQuery({
    queryKey: ["backtest-history", historySymbol],
    queryFn: ({ signal }) => getBacktestHistory(historySymbol, true, { signal }),
    enabled: historySymbol.length > 0,
    staleTime: 30000,
    retry: false,
  });
  const recent = useMemo(
    () => getRecentBacktests(),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [lab.submittedAt, lab.isSuccess]
  );
  const historyRows = useMemo(() => historyQ.data ?? [], [historyQ.data]);
  const visibleHistory = useMemo(() => historyRows.slice(0, MAX_HISTORY_ROWS), [historyRows]);

  function toggle(h) {
    setHorizons((prev) => (prev.includes(h) ? prev.filter((x) => x !== h) : [...prev, h].sort()));
  }
  function run() {
    const s = String(symbol ?? "").trim().toUpperCase();
    if (!s) {
      setFormError("Enter a symbol (e.g. AAPL, 600519.SS, MC.PA).");
      return;
    }
    if (horizons.length === 0) {
      setFormError("Select at least one horizon.");
      return;
    }
    setFormError(null);
    lab.mutate({ s, h: horizons });
  }
  function rerun(entrySymbol, entryHorizons) {
    const s = String(entrySymbol ?? "").trim().toUpperCase();
    if (!s) return;
    const h = entryHorizons.length > 0 ? [...entryHorizons].sort((a, b) => a - b) : horizons;
    if (h.length === 0) {
      setFormError("Select at least one horizon.");
      return;
    }
    setSymbol(s);
    setHorizons(h);
    setFormError(null);
    lab.mutate({ s, h });
  }
  const r = lab.data ?? undefined;

  return (
    <div className="min-w-0">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h1 className="text-sm tracking-widest text-term-muted">BACKTEST LAB · LIGHTWEIGHT (V1)</h1>
        <SourceBadge source="SOURCE: DETERMINISTIC" />
      </div>
      <section className="term-panel min-w-0 p-4" aria-labelledby="backtest-form">
        <h2 id="backtest-form" className="sr-only">Run a backtest</h2>
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label className="term-label" htmlFor="bt-symbol">Symbol</label>
            <input
              id="bt-symbol"
              className="term-input mt-1 w-48"
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
              placeholder="AAPL"
              spellCheck={false}
            />
          </div>
          <div>
            <p className="term-label" id="bt-horizons">Horizons (trading days)</p>
            <div className="mt-1 flex flex-wrap gap-2" role="group" aria-labelledby="bt-horizons">
              {FORECAST_HORIZONS.map((h) => (
                <label key={h} className="flex cursor-pointer items-center gap-1 text-sm">
                  <input type="checkbox" className="accent-term-green" checked={horizons.includes(h)} onChange={() => toggle(h)} />
                  <span className="term-num">{h}d</span>
                </label>
              ))}
            </div>
          </div>
          <button className="term-btn" type="button" disabled={lab.isPending} onClick={run}>
            {lab.isPending ? "RUNNING…" : "▶ RUN BACKTEST"}
          </button>
        </div>
        {formError && <p className="mt-2 text-xs text-term-red" role="alert">{formError}</p>}
        <p className="mt-2 text-[11px] text-term-muted">
          Walk-forward only, time-ordered splits, corporate-action adjusted prices. Brier score (0 = perfect, 0.25 = coin-flip) and ECE (lower = better calibrated).
        </p>
      </section>

      <div className="mt-4 min-w-0 space-y-4">
        {lab.isPending && (
          <div>
            <Skeleton label={`backtesting ${String(symbol ?? "").trim().toUpperCase()}…`} lines={4} variant="chart" />
            <p className="mt-1 text-[11px] text-term-muted" role="status">Walk-forward can take ~60s cold — warm cache makes repeats fast.</p>
          </div>
        )}
        {lab.isError && (
          <ErrorState
            title="Backtest failed"
            detail={`provider didn't return fresh data (${extractBackendDetail(lab.error, "Backend /api/backtest rejected")}), ${lastSuccessText(lab.dataUpdatedAt)} — backend /api/backtest unreachable or rejected.`}
            onRetry={run}
          />
        )}
        {!lab.isPending && !lab.isError && !r && (
          <EmptyState
            title="No backtest scored yet"
            detail="What: no Brier/ECE scorecard. Why: no run has completed for this symbol + horizon. Next: pick a symbol + horizon and run — failures show here too."
            actionLabel="Run backtest"
            onAction={run}
          />
        )}
        {r && (
          <>
            <LabResults r={r} />
            <section className="term-panel mt-4 flex min-w-0 flex-wrap items-center gap-2 p-4" aria-label="Backtest actions">
              <button className="term-btn text-xs" type="button" onClick={() => addToWatchlist(r.symbol, "backtest")}>
                + ADD {r.symbol} TO WATCHLIST
              </button>
              <Link className="term-btn-ghost text-xs" to={`/forecast/${encodeURIComponent(r.symbol)}`}>
                VIEW FORECAST →
              </Link>
              <Link className="term-btn-ghost text-xs" to={`/forecast/${encodeURIComponent(r.symbol)}#research`}>
                FULL RESEARCH (A/B) →
              </Link>
              <a className="text-[11px] text-term-muted hover:text-term-text" href={auditForecastsUrl(r.symbol)} target="_blank" rel="noreferrer">
                audit trail
              </a>
              <span className="text-[11px] text-term-muted">Saved to recent backtests; forecast shows this run in its calibration history.</span>
            </section>
          </>
        )}

        <section className="term-panel min-w-0 p-4" aria-labelledby="recent-backtests">
          <h2 id="recent-backtests" className="term-label">Recent backtests · this browser</h2>
          {recent.length === 0 ? (
            <EmptyState
              title="No recent backtests in this browser"
              detail="What: local run list empty. Why: no successful run has been saved here yet. Next: run a backtest above — it persists here automatically."
            />
          ) : (
            <ul className="mt-2 space-y-1 text-xs">
              {recent.map((entry) => (
                <li key={`${entry.symbol}-${entry.at}`} className="flex flex-wrap items-center justify-between gap-2 border-b border-term-border pb-1">
                  <span className="min-w-0">
                    <b className="text-term-green">{entry.symbol}</b>
                    <span className="term-num ml-2 text-term-muted">{entry.horizons.length > 0 ? entry.horizons.map((h) => `${h}d`).join(", ") : "—"}</span>
                    <span className="term-num ml-2 text-[10px] text-term-muted">{formatDateTime(entry.at)}</span>
                  </span>
                  <span className="flex gap-2">
                    <button className="term-btn-ghost text-xs" type="button" onClick={() => rerun(entry.symbol, entry.horizons)} disabled={lab.isPending}>
                      RE-RUN
                    </button>
                    <Link className="text-term-green" to={`/forecast/${encodeURIComponent(entry.symbol)}`}>
                      FORECAST →
                    </Link>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="term-panel-hero min-w-0 p-4" aria-labelledby="persisted-history">
          <div className="flex flex-wrap items-center gap-2">
            <h2 id="persisted-history" className="term-label">Persisted history · {trimmedSymbol || "—"} (backend)</h2>
            <SourceBadge source="SOURCE: DETERMINISTIC" />
          </div>
          {(historyQ.isLoading || historyQ.isFetching) && (
            <div className="mt-2"><Skeleton label="loading run history…" lines={4} variant="table" /></div>
          )}
          {historyQ.isError && (
            <ErrorState
              title="Run history unavailable"
              detail={`provider didn't return fresh data (${extractBackendDetail(historyQ.error, `backend /api/backtest/${trimmedSymbol || "…"} unreachable`)}), ${lastSuccessText(historyQ.dataUpdatedAt)}`}
              onRetry={() => void historyQ.refetch()}
            />
          )}
          {!historyQ.isLoading && !historyQ.isError && historyRows.length === 0 && (
            <EmptyState
              title={`No persisted runs for ${trimmedSymbol || "this symbol"} yet`}
              detail="What: backend run history empty. Why: no scored windows stored for this symbol. Next: run a backtest above."
              actionLabel="Run backtest"
              onAction={run}
            />
          )}
          {historyRows.length > 0 && !historyQ.isLoading && (
            <div className="mt-2 overflow-x-auto">
              {historyRows.length > visibleHistory.length && (
                <p className="mb-1 text-[11px] text-term-muted" role="status">showing first {visibleHistory.length} of {historyRows.length} runs.</p>
              )}
              <table className="w-full text-xs">
                <caption className="sr-only">Persisted backtest runs for {trimmedSymbol}</caption>
                <thead className="sticky top-0 z-10 bg-term-panel">
                  <tr className="text-left text-term-muted">
                    <th scope="col" className="py-1 pr-2">Run</th>
                    <th scope="col" className="py-1 pr-2">As of</th>
                    <th scope="col" className="py-1 pr-2">Horizons</th>
                    <th scope="col" className="py-1 pr-2 text-right">Brier</th>
                    <th scope="col" className="py-1 pr-2 text-right">ECE</th>
                    <th scope="col" className="py-1 pr-2 text-right">n</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleHistory.map((run, i) => {
                    const keys = Object.keys(run.metrics ?? {}).sort((a, b) => Number(a) - Number(b));
                    const first = keys.length > 0 ? run.metrics[keys[0]] : undefined;
                    const horizonsList = run.horizons ?? [];
                    const scored = keys.length > 0 ? keys[0] : null;
                    return (
                      <tr key={run.run_id ?? `run-${i}`} className="border-t border-term-border transition-colors duration-150 even:bg-term-panel2">
                        <td className="py-1 pr-2 font-mono text-[11px]">{String(run.run_id ?? "").slice(0, 8) || "—"}</td>
                        <td className="term-num py-1 pr-2 text-term-muted">{run.as_of ? formatDateTime(run.as_of) : "—"}</td>
                        <td className="term-num py-1 pr-2">{horizonsList.length > 0 ? horizonsList.map((h) => `${h}d`).join(", ") : "—"}</td>
                        <td className="term-num py-1 pr-2 text-right">
                          {first?.brier === null || first?.brier === undefined ? "—" : Number(first.brier).toFixed(4)}
                          {scored !== null && <span className="ml-1 text-[10px] text-term-muted">·{scored}d</span>}
                          {first?.calibrated_brier !== null && first?.calibrated_brier !== undefined && Number.isFinite(Number(first.calibrated_brier)) && (
                            <span className="ml-1 text-[10px] text-term-muted" title={`Calibrated Brier (${first.calibration_method ?? "calibrated"})`}>·cal {Number(first.calibrated_brier).toFixed(4)}</span>
                          )}
                        </td>
                        <td className="term-num py-1 pr-2 text-right">{first?.ece === null || first?.ece === undefined ? "—" : Number(first.ece).toFixed(4)}</td>
                        <td className="term-num py-1 pr-2 text-right">{first?.n_points ?? "—"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

function LabResults({ r }) {
  const slowFeed = (r.provenance?.delay_minutes ?? 0) > 30;
  const sortedHorizons = [...(r.horizons ?? [])].sort((a, b) => Number(a) - Number(b));
  const scoredHorizon = sortedHorizons[0];
  const scoredSuffix = scoredHorizon !== undefined ? ` · ${scoredHorizon}d` : "";
  const extraHorizons = sortedHorizons.length > 1 ? sortedHorizons.slice(1) : [];
  const reliability = useMemo(() => r.reliability ?? [], [r]);
  const failures = useMemo(() => r.failures ?? [], [r]);
  const visibleBins = useMemo(() => reliability.slice(0, MAX_RELIABILITY_ROWS), [reliability]);
  const verdicts = useMemo(() => {
    const out = [];
    if (r.brier === null || r.brier === undefined) {
      out.push({ ok: false, text: "Brier score unavailable — too few resolved windows." });
    } else if (r.brier <= 0.25) {
      out.push({ ok: true, text: `Brier ${r.brier.toFixed(4)} beats the coin-flip baseline (0.25).` });
    } else {
      out.push({ ok: false, text: `Brier ${r.brier.toFixed(4)} is worse than coin-flip (0.25) — model adds no skill here.` });
    }
    if (r.ece === null || r.ece === undefined) {
      out.push({ ok: false, text: "ECE unavailable." });
    } else if (r.ece <= 0.05) {
      out.push({ ok: true, text: `ECE ${r.ece.toFixed(4)} — well calibrated.` });
    } else if (r.ece <= 0.1) {
      out.push({ ok: true, text: `ECE ${r.ece.toFixed(4)} — roughly calibrated.` });
    } else {
      out.push({ ok: false, text: `ECE ${r.ece.toFixed(4)} — poorly calibrated, treat probabilities with skepticism.` });
    }
    const emptyBins = reliability.filter(
      (b) => !(typeof b?.mean_predicted === "number" && Number.isFinite(b.mean_predicted))
    ).length;
    if (emptyBins > 0) {
      out.push({ ok: false, text: `${emptyBins} calibration bin(s) empty — thin history at those probability levels.` });
    }
    for (const fl of failures) out.push({ ok: false, text: fl });
    return out;
  }, [r, reliability, failures]);

  return (
    <div className="min-w-0 space-y-4">
      {slowFeed && (
        <p className="text-[11px] text-term-muted" role="status">
          Backtest via {r.provenance?.source ?? "unknown"}, delay {r.provenance?.delay_minutes ?? "—"}m.
        </p>
      )}
      {extraHorizons.length > 0 && (
        <p className="text-[11px] text-term-muted" role="status">
          Multi-horizon run: headline Brier/ECE below score the {scoredHorizon}d horizon only; re-run per horizon for separate {extraHorizons.map((h) => `${h}d`).join(", ")} scorecards.
        </p>
      )}
      <section className="grid grid-cols-1 gap-4 md:grid-cols-3" aria-label="Backtest scorecards">
        <div className="term-panel min-w-0 p-4">
          <div className="flex flex-wrap items-center gap-2">
            <p className="term-label">Brier score · {r.symbol}{scoredSuffix} (scored)</p>
            <SourceBadge source="SOURCE: DETERMINISTIC" />
          </div>
          <p className="term-num mt-1 text-2xl font-bold text-term-text">{r.brier === null || r.brier === undefined ? "—" : r.brier.toFixed(4)}</p>
          <div className="mt-1 flex flex-wrap gap-1">
            <ProvenanceBadge p={r.provenance} />
            <FreshnessBadge p={r.provenance} />
          </div>
          <div className="mt-1"><StatusPill provenance={r.provenance} /></div>
        </div>
        <div className="term-panel min-w-0 p-4">
          <div className="flex flex-wrap items-center gap-2">
            <p className="term-label">ECE (calibration error){scoredSuffix}</p>
            <SourceBadge source="SOURCE: DETERMINISTIC" />
          </div>
          <p className="term-num mt-1 text-2xl font-bold text-term-text">{r.ece === null || r.ece === undefined ? "—" : r.ece.toFixed(4)}</p>
          {(typeof r.calibrated_brier === "number" || typeof r.calibrated_ece === "number" || typeof r.calibration_method === "string") && (
            <p className="term-num mt-1 text-[11px] text-term-muted">
              Calibrated{typeof r.calibration_method === "string" && r.calibration_method ? ` · ${r.calibration_method}` : ""}{typeof r.calibrated_brier === "number" && Number.isFinite(r.calibrated_brier) ? ` · Brier ${r.calibrated_brier.toFixed(4)}` : ""}{typeof r.calibrated_ece === "number" && Number.isFinite(r.calibrated_ece) ? ` · ECE ${r.calibrated_ece.toFixed(4)}` : ""} (cross-fitted, honest).
            </p>
          )}
          <div className="mt-1 flex flex-wrap gap-1">
            <ProvenanceBadge p={r.provenance} />
            <FreshnessBadge p={r.provenance} />
          </div>
        </div>
        <div className="term-panel min-w-0 p-4">
          <p className="term-label">Coverage</p>
          <p className="term-num mt-1 text-2xl font-bold text-term-text">
            {r.n_windows ?? "—"}
            <span className="ml-1 text-xs font-normal text-term-muted">windows</span>
          </p>
          <p className="term-num mt-1 text-xs text-term-muted">horizons: {(r.horizons ?? []).length > 0 ? (r.horizons ?? []).map((h) => `${h}d`).join(", ") : "—"}</p>
          <div className="mt-1">
            <ProvenanceBadge p={r.provenance} />
          </div>
        </div>
      </section>
      <section className="term-panel-hero min-w-0 p-4" aria-label="Reliability diagram">
        <CalibrationChart rows={reliability} title={`Reliability diagram${scoredSuffix}`} />
        <div className="mt-2 overflow-x-auto">
          {reliability.length > visibleBins.length && (
            <p className="mb-1 text-[11px] text-term-muted" role="status">showing first {visibleBins.length} of {reliability.length} bins.</p>
          )}
          <table className="w-full text-xs">
            <caption className="sr-only">Reliability table for {r.symbol}</caption>
            <thead className="sticky top-0 z-10 bg-term-panel">
              <tr className="text-left text-term-muted">
                <th scope="col" className="py-1 pr-2">Bin</th>
                <th scope="col" className="py-1 pr-2 text-right">n</th>
                <th scope="col" className="py-1 pr-2 text-right">Mean predicted</th>
                <th scope="col" className="py-1 pr-2 text-right">Fraction positive</th>
              </tr>
            </thead>
            <tbody>
              {reliability.length === 0 && (
                <tr className="border-t border-term-border">
                  <td colSpan={4} className="py-2 text-term-muted">No reliability rows returned.</td>
                </tr>
              )}
              {visibleBins.map((b, i) => (
                <tr key={`${String(b?.bin_low ?? "?")}-${String(b?.bin_high ?? "?")}-${i}`} className="border-t border-term-border transition-colors duration-150 even:bg-term-panel2">
                  <td className="term-num py-1 pr-2">
                    {typeof b?.bin_low === "number" && Number.isFinite(b.bin_low) ? b.bin_low.toFixed(2) : "—"}–
                    {typeof b?.bin_high === "number" && Number.isFinite(b.bin_high) ? b.bin_high.toFixed(2) : "—"}
                  </td>
                  <td className="term-num py-1 pr-2 text-right">{b?.count ?? "—"}</td>
                  <td className="term-num py-1 pr-2 text-right">{typeof b?.mean_predicted === "number" && Number.isFinite(b.mean_predicted) ? b.mean_predicted.toFixed(3) : "—"}</td>
                  <td className="term-num py-1 pr-2 text-right">{typeof b?.fraction_positive === "number" && Number.isFinite(b.fraction_positive) ? b.fraction_positive.toFixed(3) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="mt-2">
          <ProvenanceBadge p={r.provenance} />
        </div>
      </section>
      <section className="term-panel min-w-0 p-4" aria-label="Verdicts">
        <p className="term-label">Verdicts · successes and failures</p>
        <ul className="mt-2 space-y-1 text-xs">
          {verdicts.map((v, i) => (
            <li key={i} className={v.ok ? "text-term-green" : "text-term-red"}>{v.ok ? "✓ " : "✕ "}{v.text}</li>
          ))}
        </ul>
        {r.notes && <p className="mt-2 text-[11px] text-term-muted">{r.notes}</p>}
        <p className="mt-2 text-[11px] text-term-muted">Not investment advice.</p>
      </section>
    </div>
  );
}

export { BacktestLabPage as default };
