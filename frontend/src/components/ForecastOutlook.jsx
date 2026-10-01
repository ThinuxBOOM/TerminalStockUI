import React from "react";
import CurrencyValue from "./CurrencyValue";
import ExperimentalBadge from "./ExperimentalBadge";
import { normalizeMeasuredSkill } from "../api/measuredSkill";

// Forecast display order: the return range, volatility and drawdown risk
// lead; the up/down probability is secondary and always carries its
// measured track record, because walk-forward tests found it no better than
// the historical base rate (docs/DATA_QUALITY.md, "Measured skill").

function finite(v) {
  return typeof v === "number" && Number.isFinite(v);
}

function signedPct1(v) {
  if (!finite(v)) return "—";
  const sign = v > 0 ? "+" : v < 0 ? "−" : "";
  return `${sign}${Math.abs(v * 100).toFixed(1)}%`;
}

function signed3(v) {
  return `${v < 0 ? "−" : "+"}${Math.abs(v).toFixed(3)}`;
}

const VERDICT = {
  worse: { text: "worse than the base rate", cls: "text-term-amber" },
  indistinguishable: { text: "no better than the base rate", cls: "text-term-amber" },
  better: { text: "better than the base rate", cls: "text-term-green" },
  unknown: { text: "measured", cls: "text-term-muted" },
};

// "Measured: worse than the base rate · skill -0.031 (95% CI ...)". Accepts
// a raw `measured_skill` payload or an already-normalized entry.
function MeasuredSkillNote({ skill: raw, compact = false, className = "" }) {
  const s = normalizeMeasuredSkill(raw);
  if (!s) {
    return (
      <p className={`text-[11px] text-term-muted ${className}`} role="note">
        Direction skill not measured for this model version yet: treat the lean as unvalidated.
      </p>
    );
  }
  const v = VERDICT[s.verdict] ?? VERDICT.unknown;
  const ci = s.ci95 ? ` (95% CI ${signed3(s.ci95[0])} to ${signed3(s.ci95[1])})` : "";
  return (
    <p className={`text-[11px] ${className}`} role="note" title={s.summary || undefined}>
      <b className={v.cls}>Measured: {v.text}</b>
      <span className="text-term-muted">
        {" "}· skill {signed3(s.skill)}{ci}
        {!compact && s.symbols ? ` · walk-forward on ${s.symbols} stocks${s.as_of ? `, ${s.as_of}` : ""}` : ""}
      </span>
    </p>
  );
}

// Secondary line: the up probability, labelled as a lean, plus its record.
function DirectionLean({ forecast: f, skill, className = "" }) {
  const p = f?.probability;
  const h = f?.horizon_days;
  return (
    <div className={`rounded border border-term-border p-2 ${className}`}>
      <p className="flex flex-wrap items-center gap-2 text-xs text-term-muted">
        <span>
          Direction lean: <b className="term-num text-term-text">{finite(p) ? `${(p * 100).toFixed(1)}%` : "—"}</b> chance of rising over {h}d
        </span>
        <ExperimentalBadge status={f?.validation_status} />
      </p>
      <MeasuredSkillNote skill={skill ?? f?.measured_skill} className="mt-1" />
    </div>
  );
}

// Headline: how far the price has typically moved over this horizon.
function ForecastOutlook({ forecast: f, currency, size = "lg", className = "" }) {
  const h = f?.horizon_days;
  const iv = f?.intervals;
  const hasRange = iv && finite(iv.low) && finite(iv.high);
  const tp = f?.target_price;
  const low = Number(tp?.low);
  const high = Number(tp?.high);
  const hasPrices = tp && Number.isFinite(low) && Number.isFinite(high);
  const dd = f?.drawdown;
  const ddThreshold = Number(f?.drawdown_detail?.threshold);
  const ddLabel = Number.isFinite(ddThreshold) && ddThreshold > 0 ? `${Math.round(ddThreshold * 100)}%+` : "large";
  return (
    <div className={`min-w-0 ${className}`}>
      <p className="term-label">{h}-day range · 80% of comparable past periods</p>
      <p className={`term-num font-bold text-term-text ${size === "lg" ? "text-display-sm" : "text-2xl"}`}>
        {hasRange ? `${signedPct1(iv.low)} to ${signedPct1(iv.high)}` : "range unavailable"}
      </p>
      {hasPrices ? (
        <p className="text-xs text-term-muted">
          about <CurrencyValue value={low} currency={currency} /> to <CurrencyValue value={high} currency={currency} />
          {finite(tp.last_close) ? <> from <CurrencyValue value={tp.last_close} currency={currency} /></> : null}
        </p>
      ) : null}
      <dl className="tnum mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs">
        <div>
          <dt className="inline text-term-muted">Volatility: </dt>
          <dd className="inline font-bold text-term-text">{f?.regime ?? "unavailable"}</dd>
        </div>
        <div>
          <dt className="inline text-term-muted">Chance of a {ddLabel} drop within {h}d: </dt>
          <dd className="inline font-bold text-term-text">{finite(dd) ? `${(dd * 100).toFixed(0)}%` : "unavailable"}</dd>
        </div>
      </dl>
    </div>
  );
}

export { DirectionLean, ForecastOutlook, MeasuredSkillNote, ForecastOutlook as default };
