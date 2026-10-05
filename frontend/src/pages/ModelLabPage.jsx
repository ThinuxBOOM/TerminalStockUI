import React, { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { CheckCircle2, CircleSlash } from "lucide-react";
import { extractBackendDetail } from "../api/client";
import { getModelCard, getModelSymbol } from "../api/forecast";
import Card from "../components/ui/Card";
import Badge from "../components/ui/Badge";
import Segmented from "../components/ui/Segmented";
import Stat from "../components/ui/Stat";
import { ColumnChart, Reliability } from "../components/charts/Bars";
import ErrorState from "../components/ErrorState";
import { fmtNum, fmtPct, formatDateTime, isNum } from "../utils/format";

const HORIZONS = [1, 7, 14, 21].map((h) => ({ value: h, label: `${h} day${h > 1 ? "s" : ""}` }));

function Verdict({ ok, children }) {
  return (
    <span className={`inline-flex items-center gap-1.5 text-xs font-medium ${ok ? "text-term-green" : "text-term-muted"}`}>
      {ok ? <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" /> : <CircleSlash className="h-3.5 w-3.5" aria-hidden="true" />}
      {children}
    </span>
  );
}

function YearStrip({ values, target, format, lo, hi }) {
  const entries = Object.entries(values || {});
  if (!entries.length) return null;
  const x = (v) => `${Math.max(0, Math.min(100, ((v - lo) / (hi - lo)) * 100))}%`;
  return (
    <ul className="space-y-1.5">
      {entries.map(([year, v]) => (
        <li key={year} className="grid grid-cols-[3rem_1fr_4rem] items-center gap-3 text-xs">
          <span className="term-num text-term-muted">{year}</span>
          <span className="relative h-2 rounded-full bg-term-panel2">
            {target !== undefined ? <span className="absolute inset-y-[-3px] w-px bg-term-muted" style={{ left: x(target) }} /> : null}
            <span className="absolute top-1/2 h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-term-panel bg-term-series1" style={{ left: x(v), background: "var(--series-1)" }} />
          </span>
          <span className="term-num text-right text-term-text">{format(v)}</span>
        </li>
      ))}
    </ul>
  );
}

function SymbolLookup() {
  const [input, setInput] = useState("");
  const [symbol, setSymbol] = useState("");
  const q = useQuery({
    queryKey: ["model-symbol", symbol],
    queryFn: ({ signal }) => getModelSymbol(symbol, { signal }),
    enabled: symbol !== "",
    retry: false,
  });
  return (
    <Card title="One stock's record" subtitle="Walk-forward results for a single symbol (S&P 500 members)">
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); setSymbol(input.trim().toUpperCase()); }}>
        <input className="term-input w-40 uppercase" value={input} onChange={(e) => setInput(e.target.value)} placeholder="e.g. MSFT" aria-label="Symbol" spellCheck={false} />
        <button type="submit" className="term-btn">Look up</button>
      </form>
      {q.data ? (
        q.data.in_universe ? (
          <table className="term-table mt-4">
            <thead><tr><th>Horizon</th><th className="text-right">80% range coverage</th><th className="text-right">Outperformance hit rate</th></tr></thead>
            <tbody>
              {Object.entries(q.data.horizons).map(([h, r]) => (
                <tr key={h}>
                  <td className="text-term-text">{h} days</td>
                  <td className="term-num text-right text-term-text">{fmtPct(r.range_coverage, 1)}</td>
                  <td className="term-num text-right text-term-text">{fmtPct(r.out_hit_rate, 1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <p className="mt-3 text-sm text-term-muted">{q.data.symbol} isn&apos;t in the training universe, so there is no per-stock record.</p>
      ) : null}
      {q.isError ? <p className="mt-3 text-sm text-term-amber">{extractBackendDetail(q.error, "lookup failed")}</p> : null}
      <p className="mt-3 text-2xs text-term-faint">A single stock has few independent outcomes; expect per-stock numbers to scatter around the pooled ones above.</p>
    </Card>
  );
}

function ModelLabPage() {
  const [h, setH] = useState(21);
  const q = useQuery({ queryKey: ["model-card"], queryFn: ({ signal }) => getModelCard({ signal }), staleTime: 3600000 });
  if (q.isLoading) return <div className="h-96 animate-pulse rounded-lg bg-term-panel" />;
  if (q.isError) return <ErrorState title="Model card unavailable" detail={extractBackendDetail(q.error, "no model installed")} onRetry={() => void q.refetch()} />;
  const card = q.data;
  const r = card.report?.horizons?.[String(h)] ?? {};
  const rng = r.range ?? {};
  const dd = r.drop_risk ?? {};
  const out = r.out ?? {};
  const up = r.up ?? {};
  const deciles = (out.deciles ?? []).map((d) => ({ label: String(d.decile), value: Math.expm1(d.mean_fwd_log_return), tip: `Decile ${d.decile}${d.decile === 10 ? " (top-ranked)" : d.decile === 1 ? " (bottom-ranked)" : ""}` }));
  const ics = Object.entries(out.per_year_ic ?? {}).map(([y, v]) => ({ label: y, value: v, tip: `${y} average IC` }));
  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="type-page">Model Lab</h1>
          <p className="mt-1 max-w-2xl text-sm text-term-muted">
            How the live forecast engine performed on data it never saw. Each year from {card.report?.first_test_year ?? 2021} was predicted by a model trained only on earlier years, across {card.universe_size} S&amp;P 500 stocks.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-2xs text-term-muted">
          <Badge tone="accent">{card.version}</Badge>
          <span>trained {formatDateTime(card.trained_at)}</span>
          <span>· data {card.data_start} → {card.data_end}</span>
        </div>
      </header>

      <Segmented options={HORIZONS} value={h} onChange={setH} ariaLabel="Horizon" />

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Return range" subtitle="Does the 80% range contain 80% of outcomes?" actions={<Verdict ok={Math.abs((rng.coverage_80 ?? 0) - 0.8) < 0.02}>{Math.abs((rng.coverage_80 ?? 0) - 0.8) < 0.02 ? "Well calibrated" : "Off target"}</Verdict>}>
          <div className="grid grid-cols-3 gap-4">
            <Stat size="lg" label="Coverage" value={fmtPct(rng.coverage_80, 1)} note="target 80.0%" />
            <Stat label="Below range" value={fmtPct(rng.below_q10, 1)} note="target 10%" />
            <Stat label="Above range" value={fmtPct(rng.above_q90, 1)} note="target 10%" />
          </div>
          <div className="mt-5">
            <div className="mb-2 text-2xs font-medium uppercase tracking-[0.08em] text-term-muted">Coverage by year (line = 80%)</div>
            <YearStrip values={rng.coverage_by_year} target={0.8} lo={0.7} hi={0.9} format={(v) => fmtPct(v, 1)} />
          </div>
          <p className="mt-4 text-2xs text-term-faint">{(rng.n ?? 0).toLocaleString()} stock-days scored.</p>
        </Card>

        <Card title="Outperformance ranking" subtitle="Do higher-ranked stocks go on to beat lower-ranked ones?" actions={<Verdict ok={(out.ic_t ?? 0) >= 2}>{(out.ic_t ?? 0) >= 2 ? "Significant edge" : "Edge not significant"}</Verdict>}>
          <div className="grid grid-cols-3 gap-4">
            <Stat size="lg" label="Rank correlation" value={fmtNum(out.ic_mean, 3)} note={`t = ${out.ic_t ?? "—"}`} />
            <Stat label="Days with positive IC" value={fmtPct(out.ic_positive_share, 0)} />
            <Stat label="Top − bottom decile" value={fmtPct(out.decile_spread, 2, { signed: true })} note={`per ${h}d (log)`} />
          </div>
          <div className="mt-4">
            <div className="mb-1 text-2xs font-medium uppercase tracking-[0.08em] text-term-muted">Average {h}-day return by model decile</div>
            <ColumnChart data={deciles} format={(v) => fmtPct(v, 2, { signed: true })} height={190} xLabel="1 = lowest-ranked · 10 = highest-ranked" ariaLabel="Forward return by decile" />
          </div>
          <div className="mt-4">
            <div className="mb-1 text-2xs font-medium uppercase tracking-[0.08em] text-term-muted">Rank correlation by year</div>
            <ColumnChart data={ics} format={(v) => fmtNum(v, 3)} height={150} ariaLabel="IC by year" />
          </div>
        </Card>

        <Card title="Drop risk" subtitle={`Probability of a 10%+ drop within ${h} days`} actions={<Verdict ok={(dd.skill ?? 0) > 0}>{(dd.skill ?? 0) > 0 ? "Beats base rate" : "No edge"}</Verdict>}>
          <div className="grid grid-cols-3 gap-4">
            <Stat size="lg" label="Brier skill" value={fmtPct(dd.skill, 1, { signed: true })} note="vs. base rate" />
            <Stat label="Event rate" value={fmtPct(dd.event_rate, 1)} />
            <Stat label="Brier score" value={fmtNum(dd.brier_model, 3)} note={`base rate ${fmtNum(dd.brier_base, 3)} (lower is better)`} />
          </div>
          <p className="mt-4 text-sm leading-relaxed text-term-muted">
            Drop risk comes from the same volatility forecast as the range: when a stock&apos;s expected volatility rises, so does its chance of a 10%+ fall. It is most useful at 7–21 days; one-day drops of that size are too rare to forecast.
          </p>
        </Card>

        <Card title="Chance of rising" subtitle="Absolute direction, against the historical base rate" actions={<Verdict ok={isNum(up.skill_ci95?.[0]) && up.skill_ci95[0] > 0}>{isNum(up.skill_ci95?.[0]) && up.skill_ci95[0] > 0 ? "Beats base rate" : "No reliable edge"}</Verdict>}>
          <div className="grid grid-cols-3 gap-4">
            <Stat size="lg" label="Brier skill" value={fmtPct(up.skill, 2, { signed: true })} note={up.skill_ci95 ? `95% CI ${fmtPct(up.skill_ci95[0], 2, { signed: true })} to ${fmtPct(up.skill_ci95[1], 2, { signed: true })}` : undefined} />
            <Stat label="Up share" value={fmtPct(up.up_share, 1)} note="how often stocks rose" />
            <Stat label="Model weight" value={fmtPct(up.blend, 0)} note="rest is the base rate" />
          </div>
          {up.reliability?.length ? <div className="mt-4"><Reliability rows={up.reliability} height={190} /></div> : null}
        </Card>
      </div>

      <SymbolLookup />

      <Card title="Method">
        <ul className="list-disc space-y-1.5 pl-5 text-sm text-term-muted">
          <li>Walk-forward by calendar year with an embargo of {h} trading days between training and test data, so no training label overlaps a test period.</li>
          <li>Ranges come from a volatility model (log-HAR) fitted across all stocks; quantiles of returns scaled by forecast volatility.</li>
          <li>The ranking model is a regularized logistic regression on features ranked across stocks each day (momentum, reversal, trend, volatility, volume, sector-relative momentum).</li>
          <li>Confidence intervals use a block bootstrap over months, because stocks on the same day move together.</li>
          <li>Universe is today&apos;s S&amp;P 500, so results carry survivorship bias. The model is retrained every week; this page always shows the active version.</li>
        </ul>
        <p className="mt-3 text-sm"><Link to="/screener" className="term-link">Use the ranking in the screener →</Link></p>
      </Card>
    </div>
  );
}

export { ModelLabPage as default };
