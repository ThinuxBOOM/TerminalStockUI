import React, { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { FORECAST_HORIZONS, auditForecastsUrl, runBacktest } from "../api/client";
import { getBacktestHistory, getRecentBacktests, saveRecentBacktest } from "../api/backtestHistory";
import useWatchlist from "../hooks/useWatchlist";
import ProvenanceBadge from "../components/ProvenanceBadge";
import FreshnessBadge from "../components/FreshnessBadge";
import CalibrationChart from "../components/CalibrationChart";
import { SourceBadge } from "../components/ResearchSection";
import Loading from "../components/Loading";
import ErrorState, { StaleBanner } from "../components/ErrorState";

const MAX_HISTORY_ROWS = 20;
const MAX_RELIABILITY_ROWS = 20;

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
  const trimmedSymbol = symbol.trim().toUpperCase();
  // History follows a debounced symbol: typing "AAPL" must not fire
  // four sequential history fetches.
  const [historySymbol, setHistorySymbol] = useState(trimmedSymbol);
  useEffect(() => {
    const t = setTimeout(() => setHistorySymbol(symbol.trim().toUpperCase()), 500);
    return () => clearTimeout(t);
  }, [symbol]);
  const historyQ = useQuery({
    queryKey: ["backtest-history", historySymbol],
    queryFn: () => getBacktestHistory(historySymbol, true),
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
    const s = symbol.trim().toUpperCase();
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
    const s = entrySymbol.trim().toUpperCase();
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
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h1 className="text-sm tracking-widest text-term-muted">BACKTEST LAB · LIGHTWEIGHT (V1)</h1>
        <SourceBadge source="SOURCE: DETERMINISTIC" />
      </div>
      <section className="term-panel p-4">
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
            <p className="term-label">Horizons (trading days)</p>
            <div className="mt-1 flex gap-2">
              {FORECAST_HORIZONS.map((h) => (
                <label key={h} className="flex cursor-pointer items-center gap-1 text-sm">
                  <input type="checkbox" checked={horizons.includes(h)} onChange={() => toggle(h)} />
                  {h}d
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

      <div className="mt-4">
        {lab.isPending && <Loading label={`backtesting ${symbol.trim().toUpperCase()}…`} />}
        {lab.isError && (
          <ErrorState
            title="Backtest failed"
            detail={lab.error instanceof Error ? `${lab.error.message} — backend /api/backtest unreachable or rejected.` : "Backend /api/backtest unreachable or rejected."}
            onRetry={run}
          />
        )}
        {!lab.isPending && !lab.isError && !r && (
          <div className="term-panel p-6 text-sm text-term-muted">
            Pick a symbol + horizon and run. Results show Brier score, ECE, and the reliability table — failures included.
          </div>
        )}
        {r && (
          <>
            <LabResults r={r} />
            <section className="term-panel mt-4 flex flex-wrap items-center gap-2 p-4">
              <button className="term-btn" type="button" onClick={() => addToWatchlist(r.symbol, "backtest")}>
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

        <section className="term-panel mt-4 p-4">
          <p className="term-label">Recent backtests · this browser</p>
          {recent.length === 0 ? (
            <p className="mt-1 text-xs text-term-muted">No backtests run yet in this browser — runs persist here after each success.</p>
          ) : (
            <ul className="mt-2 space-y-1 text-xs">
              {recent.map((entry) => (
                <li key={`${entry.symbol}-${entry.at}`} className="flex flex-wrap items-center justify-between gap-2 border-b border-term-border pb-1">
                  <span>
                    <b className="text-term-green">{entry.symbol}</b>
                    <span className="ml-2 text-term-muted">{entry.horizons.length > 0 ? entry.horizons.map((h) => `${h}d`).join(", ") : "—"}</span>
                    <span className="ml-2 text-[10px] text-term-muted">{new Date(entry.at).toLocaleString()}</span>
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

        <section className="term-panel mt-4 p-4">
          <div className="flex flex-wrap items-center gap-2">
            <p className="term-label">Persisted history · {trimmedSymbol || "—"} (backend)</p>
            <SourceBadge source="SOURCE: DETERMINISTIC" />
          </div>
          {historyQ.isLoading && <p className="mt-1 text-xs text-term-muted">loading run history…</p>}
          {historyQ.isError && <p className="mt-1 text-xs text-term-amber">⚠ run history unavailable — backend /api/backtest/{trimmedSymbol || "…"} unreachable.</p>}
          {!historyQ.isLoading && !historyQ.isError && historyRows.length === 0 && (
            <p className="mt-1 text-xs text-term-muted">No persisted runs for {trimmedSymbol || "this symbol"} yet — run a backtest above.</p>
          )}
          {historyRows.length > 0 && (
            <div className="mt-2 overflow-x-auto">
              {historyRows.length > visibleHistory.length && (
                <p className="mb-1 text-[11px] text-term-muted" role="status">showing first {visibleHistory.length} of {historyRows.length} runs.</p>
              )}
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-left text-term-muted">
                    <th className="py-1 pr-2">Run</th>
                    <th className="py-1 pr-2">As of</th>
                    <th className="py-1 pr-2">Horizons</th>
                    <th className="py-1 pr-2">Brier</th>
                    <th className="py-1 pr-2">ECE</th>
                    <th className="py-1 pr-2">n</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleHistory.map((run, i) => {
                    const keys = Object.keys(run.metrics ?? {});
                    const first = keys.length > 0 ? run.metrics[keys[0]] : undefined;
                    const horizonsList = run.horizons ?? [];
                    const scored = keys.length > 0 ? keys[0] : null;
                    return (
                      <tr key={run.run_id ?? `run-${i}`} className="border-t border-term-border">
                        <td className="py-1 pr-2 font-mono text-[11px]">{String(run.run_id ?? "").slice(0, 8) || "—"}</td>
                        <td className="py-1 pr-2 text-term-muted">{run.as_of ?? "—"}</td>
                        <td className="py-1 pr-2">{horizonsList.length > 0 ? horizonsList.map((h) => `${h}d`).join(", ") : "—"}</td>
                        <td className="py-1 pr-2">
                          {first?.brier === null || first?.brier === undefined ? "—" : Number(first.brier).toFixed(4)}
                          {scored !== null && <span className="ml-1 text-[10px] text-term-muted">·{scored}d</span>}
                        </td>
                        <td className="py-1 pr-2">{first?.ece === null || first?.ece === undefined ? "—" : Number(first.ece).toFixed(4)}</td>
                        <td className="py-1 pr-2">{first?.n_points ?? "—"}</td>
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
  const stale = r.provenance?.fallback_used === true || (r.provenance?.delay_minutes ?? 0) > 30;
  const scoredHorizon = (r.horizons ?? [])[0];
  const scoredSuffix = scoredHorizon !== undefined ? ` · ${scoredHorizon}d` : "";
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
    <div className="space-y-4">
      {stale && <StaleBanner detail={`backtest via ${r.provenance?.source ?? "unknown"}, delay ${r.provenance?.delay_minutes ?? "—"}m`} />}
      <section className="grid gap-4 md:grid-cols-3">
        <div className="term-panel p-4">
          <div className="flex flex-wrap items-center gap-2">
            <p className="term-label">Brier score · {r.symbol}{scoredSuffix}</p>
            <SourceBadge source="SOURCE: DETERMINISTIC" />
          </div>
          <p className="mt-1 text-2xl font-bold">{r.brier === null || r.brier === undefined ? "—" : r.brier.toFixed(4)}</p>
          <div className="mt-1 flex flex-wrap gap-1">
            <ProvenanceBadge p={r.provenance} />
            <FreshnessBadge p={r.provenance} />
          </div>
        </div>
        <div className="term-panel p-4">
          <div className="flex flex-wrap items-center gap-2">
            <p className="term-label">ECE (calibration error){scoredSuffix}</p>
            <SourceBadge source="SOURCE: DETERMINISTIC" />
          </div>
          <p className="mt-1 text-2xl font-bold">{r.ece === null || r.ece === undefined ? "—" : r.ece.toFixed(4)}</p>
          <div className="mt-1 flex flex-wrap gap-1">
            <ProvenanceBadge p={r.provenance} />
            <FreshnessBadge p={r.provenance} />
          </div>
        </div>
        <div className="term-panel p-4">
          <p className="term-label">Coverage</p>
          <p className="mt-1 text-2xl font-bold">
            {r.n_windows ?? "—"}
            <span className="ml-1 text-xs font-normal text-term-muted">windows</span>
          </p>
          <p className="mt-1 text-xs text-term-muted">horizons: {(r.horizons ?? []).length > 0 ? (r.horizons ?? []).map((h) => `${h}d`).join(", ") : "—"}</p>
          <div className="mt-1">
            <ProvenanceBadge p={r.provenance} />
          </div>
        </div>
      </section>
      <section className="term-panel p-4">
        <CalibrationChart rows={reliability} title={`Reliability diagram${scoredSuffix}`} />
        <div className="mt-2 overflow-x-auto">
          {reliability.length > visibleBins.length && (
            <p className="mb-1 text-[11px] text-term-muted" role="status">showing first {visibleBins.length} of {reliability.length} bins.</p>
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
              {reliability.length === 0 && (
                <tr className="border-t border-term-border">
                  <td colSpan={4} className="py-2 text-term-muted">No reliability rows returned.</td>
                </tr>
              )}
              {visibleBins.map((b, i) => (
                <tr key={`${String(b?.bin_low ?? "?")}-${String(b?.bin_high ?? "?")}-${i}`} className="border-t border-term-border">
                  <td className="py-1 pr-2">
                    {typeof b?.bin_low === "number" && Number.isFinite(b.bin_low) ? b.bin_low.toFixed(2) : "—"}–
                    {typeof b?.bin_high === "number" && Number.isFinite(b.bin_high) ? b.bin_high.toFixed(2) : "—"}
                  </td>
                  <td className="py-1 pr-2">{b?.count ?? "—"}</td>
                  <td className="py-1 pr-2">{typeof b?.mean_predicted === "number" && Number.isFinite(b.mean_predicted) ? b.mean_predicted.toFixed(3) : "—"}</td>
                  <td className="py-1 pr-2">{typeof b?.fraction_positive === "number" && Number.isFinite(b.fraction_positive) ? b.fraction_positive.toFixed(3) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="mt-2">
          <ProvenanceBadge p={r.provenance} />
        </div>
      </section>
      <section className="term-panel p-4">
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
