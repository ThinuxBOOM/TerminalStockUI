import React from "react";
import { Link } from "react-router-dom";
import { CheckCircle2, Info, TrendingDown, TrendingUp } from "lucide-react";
import Card from "../../components/ui/Card";
import Badge from "../../components/ui/Badge";
import Segmented from "../../components/ui/Segmented";
import Stat from "../../components/ui/Stat";
import { FanChart } from "../../components/charts/FanChart";
import { RangeBar, RankMeter } from "../../components/charts/Inline";
import { fmtMoney, fmtNum, fmtPct, fmtRank, isNum } from "../../utils/format";

const HORIZON_OPTIONS = [1, 7, 14, 21].map((h) => ({ value: h, label: `${h}D`, title: `${h} trading day${h > 1 ? "s" : ""}` }));

function strengthBadge(f) {
  if (!f?.relative_available || !isNum(f?.outperform_rank)) return null;
  const s = f.signal_strength;
  const up = f.outperform_rank >= 0.5;
  if (s === "weak") return <Badge>Weak signal</Badge>;
  return <Badge tone={up ? "positive" : "negative"}>{s === "strong" ? "Strong" : "Moderate"} {up ? "outperform" : "underperform"} signal</Badge>;
}

// Compact card for the Overview tab: range first, then rank and risk.
function ForecastSummaryCard({ forecasts, horizon, onHorizon, currency, onOpen, loading, error }) {
  const f = forecasts?.[horizon];
  return (
    <Card
      title="Forecast"
      subtitle="Range from the volatility model · rank vs. S&P 500"
      actions={<Segmented options={HORIZON_OPTIONS} value={horizon} onChange={onHorizon} ariaLabel="Forecast horizon" size="xs" />}
    >
      {loading ? <div className="h-40 animate-pulse rounded-md bg-term-panel2" /> : null}
      {!loading && error ? <p className="text-sm text-term-muted">{error}</p> : null}
      {!loading && f ? (
        <div className="space-y-4">
          <div>
            <div className="text-2xs font-medium uppercase tracking-[0.08em] text-term-muted">Likely {f.horizon_days}-day range (80%)</div>
            <div className="mt-1 flex items-baseline gap-2">
              <span className="text-2xl font-semibold tracking-tight text-term-text">
                {fmtPct(f.expected_return_range.low, 1, { signed: true })} to {fmtPct(f.expected_return_range.high, 1, { signed: true })}
              </span>
            </div>
            <div className="mt-0.5 text-xs text-term-muted">
              {fmtMoney(f.target_price?.low, currency)} – {fmtMoney(f.target_price?.high, currency)}
            </div>
            <RangeBar low={f.expected_return_range.low} mid={f.expected_return_range.mid} high={f.expected_return_range.high} className="mt-2" height={10} />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <div className="text-2xs font-medium uppercase tracking-[0.08em] text-term-muted">Outperformance rank</div>
              {f.relative_available ? (
                <>
                  <div className="mt-1 text-lg font-semibold text-term-text">{fmtRank(f.outperform_rank)}</div>
                  <RankMeter value={f.outperform_rank} className="mt-1.5" />
                </>
              ) : (
                <div className="mt-1 text-xs text-term-muted">US listings only</div>
              )}
            </div>
            <Stat label="10%+ drop risk" value={fmtPct(f.drawdown_probability, 0)} note={`within ${f.horizon_days}d`} />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {strengthBadge(f)}
            <Badge title="Annualized volatility forecast">Vol {fmtPct(f.volatility_forecast_annual, 0)} · {f.volatility_regime}</Badge>
          </div>
          <button type="button" className="term-link text-sm" onClick={onOpen}>Full forecast and track record →</button>
        </div>
      ) : null}
    </Card>
  );
}

function MeasuredRecord({ m, horizon }) {
  if (!m || !isNum(m.range_coverage_80)) return null;
  const items = [
    { label: "80% range coverage", value: fmtPct(m.range_coverage_80, 1), good: Math.abs(m.range_coverage_80 - 0.8) < 0.02, note: "target 80%" },
    { label: "Drop-risk skill", value: fmtPct(m.drop_risk_skill, 1, { signed: true }), good: m.drop_risk_skill > 0, note: "vs. base rate" },
    { label: "Ranking edge (IC)", value: fmtNum(m.out_ic, 3), good: (m.out_ic_t ?? 0) >= 2, note: `t = ${m.out_ic_t ?? "—"}` },
    { label: "Top vs. bottom decile", value: fmtPct(m.out_decile_spread, 2, { signed: true }), good: m.out_decile_spread > 0, note: `per ${horizon}d` },
    { label: "Chance-of-rising skill", value: fmtPct(m.up_skill, 2, { signed: true }), good: false, note: "≈ base rate" },
  ];
  return (
    <Card
      title="How accurate is this?"
      subtitle={`Walk-forward test, 2021–2026, ~500 S&P 500 stocks · ${horizon}-day horizon`}
      actions={<Link to="/model" className="term-link text-xs">Model Lab →</Link>}
    >
      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3 lg:grid-cols-5">
        {items.map((it) => (
          <div key={it.label} className="min-w-0">
            <dt className="truncate text-2xs font-medium uppercase tracking-[0.08em] text-term-muted">{it.label}</dt>
            <dd className="mt-0.5 flex items-center gap-1.5 text-base font-semibold text-term-text">
              {it.good ? <CheckCircle2 className="h-3.5 w-3.5 text-term-green" aria-label="measured edge" /> : null}
              <span className="term-num">{it.value}</span>
            </dd>
            <dd className="text-2xs text-term-faint">{it.note}</dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}

function DriverList({ items, tone }) {
  if (!items?.length) return <p className="text-sm text-term-muted">None stand out.</p>;
  return (
    <ul className="space-y-2">
      {items.map((d) => (
        <li key={d.feature} className="flex items-start gap-2 text-sm">
          {tone === "for" ? <TrendingUp className="mt-0.5 h-4 w-4 shrink-0 text-term-green" aria-hidden="true" /> : <TrendingDown className="mt-0.5 h-4 w-4 shrink-0 text-term-red" aria-hidden="true" />}
          <span className="min-w-0">
            <span className="text-term-text">{d.label[0].toUpperCase() + d.label.slice(1)}</span>
            {isNum(d.percentile) ? <span className="text-term-muted"> · {d.percentile >= 0.5 ? `higher than ${Math.round(d.percentile * 100)}%` : `lower than ${Math.round((1 - d.percentile) * 100)}%`} of stocks</span> : null}
          </span>
        </li>
      ))}
    </ul>
  );
}

function ForecastTab({ forecasts, horizon, onHorizon, currency, loading, error, onRetry }) {
  if (loading) return <div className="h-80 animate-pulse rounded-lg bg-term-panel" />;
  if (error) {
    return (
      <Card title="Forecast unavailable">
        <p className="text-sm text-term-muted">{error}</p>
        {onRetry ? <button type="button" className="term-btn-ghost mt-3 text-sm" onClick={onRetry}>Retry</button> : null}
      </Card>
    );
  }
  const f = forecasts?.[horizon];
  if (!f) return null;
  const any = Object.values(forecasts)[0];
  return (
    <div className="space-y-4">
      <Card title="Where the price may go" subtitle="Ranges of outcomes from the volatility model, by horizon. Hover for prices.">
        <FanChart horizons={forecasts} lastClose={any?.target_price?.last_close} currency={currency} />
        <p className="mt-3 text-sm leading-relaxed text-term-muted">{f.summary}</p>
      </Card>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-term-text">Detail for {f.horizon_days} trading day{f.horizon_days > 1 ? "s" : ""}</h2>
        <Segmented options={HORIZON_OPTIONS} value={horizon} onChange={onHorizon} ariaLabel="Forecast horizon" />
      </div>

      <div className="grid gap-4 md:grid-cols-2 2xl:grid-cols-3">
        <Card title="Return range" subtitle="80% of comparable past periods">
          <div className="text-xl font-semibold text-term-text">
            {fmtPct(f.expected_return_range.low, 1, { signed: true })} to {fmtPct(f.expected_return_range.high, 1, { signed: true })}
          </div>
          <div className="mt-1 text-sm text-term-muted">{fmtMoney(f.target_price?.low, currency)} – {fmtMoney(f.target_price?.high, currency)}</div>
          <RangeBar low={f.expected_return_range.low} mid={f.expected_return_range.mid} high={f.expected_return_range.high} className="mt-3" height={10} />
          <dl className="mt-4 grid grid-cols-2 gap-3">
            <Stat size="sm" label="Median" value={fmtPct(f.expected_return_range.mid, 1, { signed: true })} />
            <Stat size="sm" label="Volatility (ann.)" value={fmtPct(f.volatility_forecast_annual, 0)} note={`regime: ${f.volatility_regime}`} />
            <Stat size="sm" label="10%+ drop risk" value={fmtPct(f.drawdown_probability, 0)} />
            <Stat size="sm" label="90% range" value={`${fmtPct(f.quantiles?.["0.05"], 0, { signed: true })} / ${fmtPct(f.quantiles?.["0.95"], 0, { signed: true })}`} />
          </dl>
        </Card>

        <Card title="Outperformance vs. S&P 500" subtitle="Chance of beating the median stock">
          {f.relative_available ? (
            <>
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-xl font-semibold text-term-text">{fmtRank(f.outperform_rank)}</span>
                <span className="term-num text-sm text-term-muted">{fmtPct(f.outperform_probability, 1)} chance</span>
              </div>
              <RankMeter value={f.outperform_rank} className="mt-2" height={8} />
              <div className="mt-2">{strengthBadge(f)}</div>
              <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-1">
                <div>
                  <div className="mb-2 text-2xs font-medium uppercase tracking-[0.08em] text-term-muted">Pushing it up</div>
                  <DriverList items={f.drivers.for} tone="for" />
                </div>
                <div>
                  <div className="mb-2 text-2xs font-medium uppercase tracking-[0.08em] text-term-muted">Holding it back</div>
                  <DriverList items={f.drivers.against} tone="against" />
                </div>
              </div>
            </>
          ) : (
            <p className="text-sm text-term-muted">The ranking model is trained on S&P 500 stocks, so it isn&apos;t applied to this listing. Range and drop risk above still apply.</p>
          )}
        </Card>

        <Card title="Chance of rising" subtitle="Close to the historical base rate, by design">
          <div className="flex items-baseline gap-2">
            <span className="text-xl font-semibold text-term-text">{fmtPct(f.direction_probability, 1)}</span>
            <span className="text-sm text-term-muted">base rate {fmtPct(f.base_rate, 1)}</span>
          </div>
          <p className="mt-3 text-sm leading-relaxed text-term-muted">
            Across ten years of S&amp;P 500 data, no model predicted the <em>direction</em> of individual stocks better than how often they historically rose. This number therefore stays near the base rate and only moves as far as testing supports.
          </p>
          <div className="mt-3 flex items-start gap-2 rounded-md border border-term-border bg-term-panel2 p-2.5 text-xs text-term-muted">
            <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-term-accent" aria-hidden="true" />
            Use the range to size risk and the outperformance rank to compare stocks, rather than this number to pick a side.
          </div>
        </Card>
      </div>

      <MeasuredRecord m={f.measured} horizon={f.horizon_days} />

      <Card title="Limitations">
        <ul className="list-disc space-y-1 pl-5 text-sm text-term-muted">
          {f.limitations.map((l) => <li key={l}>{l}</li>)}
        </ul>
        <p className="mt-3 text-2xs text-term-faint">
          Model {f.model_version} · data {f.data_version} · cross-section {f.cross_section_as_of ?? "—"} · {f.disclosure}
        </p>
      </Card>
    </div>
  );
}

export { ForecastSummaryCard, ForecastTab, HORIZON_OPTIONS, MeasuredRecord };
