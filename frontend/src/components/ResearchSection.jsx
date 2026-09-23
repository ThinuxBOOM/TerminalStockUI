import React from "react";
import ProvenanceBadge from "./ProvenanceBadge";
import FreshnessBadge from "./FreshnessBadge";
import CalibrationChart from "./CalibrationChart";
import AIOpinionCard from "./AIOpinionCard";
import {
  AI_DISABLED_LABEL,
  AI_WEIGHT_CAP,
  PLAN_TIERS,
  TIER_FEATURES,
  auditForecastsUrl,
  blendProbs,
  clampAIWeight,
  sourceLabelForAIOpinion,
} from "../api/client";
import { formatPct1 } from "../utils/format";

// Explicit source badges — every research number carries its origin.
function SourceBadge({ source }) {
  const isAI = String(source ?? "").toUpperCase().startsWith("SOURCE: AI");
  const isDisabled = String(source ?? "").toUpperCase().startsWith("AI DISABLED");
  const cls = isDisabled
    ? "border-term-border text-term-muted"
    : isAI
      ? "border-term-amber text-term-amber"
      : "border-term-green text-term-green";
  return (
    <span
      className={`rounded border px-2 py-0.5 text-[10px] font-bold tracking-widest ${cls}`}
      title={isAI ? "Narrative from an AI provider — bounded, never overrides the deterministic core" : "Computed by the deterministic quant engine"}
    >
      {source}
    </span>
  );
}

// Blended forecast bar: blended = (1-w)*quant + w*ai, w <= AI_WEIGHT_CAP.
// Never overrides quant with AI; malformed AI degrades to quant alone.
// Numbers via shared formatPct1 (one decimal) + tabular-nums for stability.
function BlendedForecastBar({ quantProb, aiProb, aiWeight }) {
  const w = clampAIWeight(aiWeight);
  const q = typeof quantProb === "number" && Number.isFinite(quantProb) ? quantProb : null;
  const a = typeof aiProb === "number" && Number.isFinite(aiProb) ? aiProb : null;
  const blended = blendProbs(q, a, w);
  if (q === null || blended === null) return null;
  const pct = (v) => formatPct1(v);
  const quantWidth = Math.min(100, Math.max(0, q * 100));
  const blendedWidth = Math.min(100, Math.max(0, blended * 100));
  return (
    <div className="term-panel-nested p-2" role="group" aria-label="blended forecast">
      <p className="text-[11px] text-term-muted">
        Blended forecast <span className="font-mono">(1-w)*quant + w*ai</span>, w={w.toFixed(2)} (cap {AI_WEIGHT_CAP.toFixed(2)})
      </p>
      <p className="term-num mt-1 text-xs">
        ({(1 - w).toFixed(2)} × {pct(q)}) + ({w.toFixed(2)} × {a !== null ? pct(a) : "—"}) = <b>{pct(blended)}</b>
      </p>
      <div className="mt-1 h-2 w-full rounded bg-term-border" title={`quant ${pct(q)}`}>
        <div className="h-2 rounded bg-term-green" style={{ width: `${quantWidth}%` }} />
      </div>
      <div className="mt-1 h-2 w-full rounded bg-term-border" title={`blended ${pct(blended)}`}>
        <div className="h-2 rounded bg-term-cyan" style={{ width: `${blendedWidth}%` }} />
      </div>
      <p className="mt-1 text-[10px] text-term-muted">
        Quant {pct(q)} vs blended {pct(blended)} — AI moves the forecast at most {(AI_WEIGHT_CAP * 100).toFixed(0)}% of the way toward its figure.
      </p>
    </div>
  );
}

function AuditLink({ symbol, limit = 20 }) {
  if (!symbol) return null;
  const href = auditForecastsUrl(symbol, limit);
  return (
    <a
      className="term-btn-ghost inline-block text-xs"
      href={href}
      target="_blank"
      rel="noreferrer"
      title={`GET ${href}`}
    >
      AUDIT TRAIL →
    </a>
  );
}

// Tier-gated UI stub (future-proof, NOT enforced).
// Plan mapping: Free -> Deep Research locked; Silver -> Report locked;
// Gold/Platinum -> all unlocked. Always render with locked=false for now.
function DeepResearchStub({ locked = false, tier = "Free", feature = "Deep Research" }) {
  const meta = TIER_FEATURES[feature] ?? { minTier: "Silver", lockedIcon: "🔒" };
  const tierIndex = PLAN_TIERS.indexOf(tier);
  const minIndex = PLAN_TIERS.indexOf(meta.minTier);
  const wouldLock = locked && tierIndex >= 0 && tierIndex < minIndex;
  if (!wouldLock) {
    // Unlocked stub: visible affordance, no gating. Locked path below is
    // future UI only — locked is always false until plans launch.
    return (
      <div className="rounded border border-dashed border-term-border p-2 text-xs text-term-muted" role="note">
        <p className="font-bold text-term-text">{feature} <span className="text-[10px] font-normal text-term-muted">(coming soon — {PLAN_TIERS.join("/")})</span></p>
        <p className="mt-0.5">Tier-gated stub: Free/Silver/Gold/Platinum mapping lives in client.TIER_FEATURES. No enforcement yet.</p>
      </div>
    );
  }
  return (
    <div className="rounded border border-dashed border-term-amber p-2 text-xs text-term-amber" role="note">
      <p className="font-bold">{meta.lockedIcon} {feature} — {tier} tier</p>
      <p className="mt-0.5">Upgrade to {meta.minTier}+ to unlock. (Stub — not enforced.)</p>
    </div>
  );
}

// (A) Deterministic Engine block.
function DeterministicEngineBlock({ forecast, calibrationHistory = [], maxWhy = 4 }) {
  if (!forecast) return null;
  const f = forecast;
  const versions = f.versions ?? {};
  const modelVersion = versions.model_version ?? versions.model_name ?? "—";
  const featureVersion = versions.feature_version ?? "—";
  const dataVersion = versions.data_version ?? "—";
  const why = (f.why ?? []).slice(0, maxWhy);
  const risks = (f.risks ?? []).slice(0, maxWhy);
  const evidence = f.evidence_ids ?? [];
  const iv = f.intervals ?? null;
  const sparkRows = (f.calibration ?? []).length > 0 ? f.calibration : (calibrationHistory[0]?.reliability ?? []);
  return (
    <section className="term-panel-nested border-term-green/40 p-3" aria-label="deterministic engine">
      <div className="flex flex-wrap items-center gap-2">
        <p className="term-label">(A) Deterministic Engine</p>
        <SourceBadge source="SOURCE: DETERMINISTIC" />
        <FreshnessBadge p={f.provenance} />
      </div>
      <p className="term-num mt-1 text-2xl font-bold text-term-green">
        {formatPct1(f.probability)} <span className="text-xs font-normal text-term-muted">direction probability · {f.horizon_days}d</span>
      </p>
      <dl className="mt-2 grid grid-cols-2 gap-2 text-xs md:grid-cols-4">
        <div className="term-panel-nested p-2">
          <dt className="text-term-muted">model_version</dt>
          <dd className="truncate font-bold" title={String(modelVersion)}>{String(modelVersion)}</dd>
        </div>
        <div className="term-panel-nested p-2">
          <dt className="text-term-muted">feature_version</dt>
          <dd className="truncate font-bold" title={String(featureVersion)}>{String(featureVersion)}</dd>
        </div>
        <div className="term-panel-nested p-2">
          <dt className="text-term-muted">data_version</dt>
          <dd className="truncate font-bold" title={String(dataVersion)}>{String(dataVersion)}</dd>
        </div>
        <div className="term-panel-nested p-2">
          <dt className="text-term-muted">confidence</dt>
          <dd className="font-bold">{f.confidence}</dd>
        </div>
        <div className="term-panel-nested p-2">
          <dt className="text-term-muted">expected range</dt>
          <dd className="term-num font-bold">
            {iv && Number.isFinite(iv.low) && Number.isFinite(iv.mid) && Number.isFinite(iv.high)
              ? `${(iv.low * 100).toFixed(1)}% / ${(iv.mid * 100).toFixed(1)}% / ${(iv.high * 100).toFixed(1)}%`
              : "unavailable"}
          </dd>
        </div>
        <div className="term-panel-nested p-2">
          <dt className="text-term-muted">regime</dt>
          <dd className="font-bold">{f.regime ?? "unavailable"}</dd>
        </div>
        <div className="term-panel-nested p-2">
          <dt className="text-term-muted">drawdown</dt>
          <dd className="term-num font-bold">{typeof f.drawdown === "number" && Number.isFinite(f.drawdown) ? `${(f.drawdown * 100).toFixed(1)}%` : "unavailable"}</dd>
        </div>
        <div className="term-panel-nested p-2">
          <dt className="text-term-muted">quality</dt>
          <dd className="term-num font-bold text-term-cyan">{f.quality_grade}</dd>
        </div>
      </dl>
      <div className="mt-2 grid gap-2 text-xs md:grid-cols-2">
        <div className="term-panel-nested p-2">
          <p className="font-bold text-term-green">Why (≤{maxWhy})</p>
          {why.length === 0 ? <p className="text-term-muted">unavailable</p> : (
            <ul className="list-disc pl-4 text-term-muted">{why.map((w, i) => <li key={`${w}-${i}`}>{w}</li>)}</ul>
          )}
        </div>
        <div className="term-panel-nested p-2">
          <p className="font-bold text-term-red">Risks (≤{maxWhy})</p>
          {risks.length === 0 ? <p className="text-term-muted">unavailable</p> : (
            <ul className="list-disc pl-4 text-term-muted">{risks.map((w, i) => <li key={`${w}-${i}`}>{w}</li>)}</ul>
          )}
        </div>
      </div>
      <div className="mt-2 text-xs">
        <p className="text-term-muted">evidence_ids (= model_members): {evidence.length === 0 ? <span>none</span> : evidence.slice(0, 24).map((e, i) => (
          <code key={`${e}-${i}`} className="mr-1 rounded bg-term-bg px-1 py-0.5 text-term-cyan">{e}</code>
        ))}{evidence.length > 24 && <span className="text-term-muted">… +{evidence.length - 24} more</span>}</p>
      </div>
      {sparkRows.length > 0 && (
        <div className="mt-2">
          <CalibrationChart rows={sparkRows} variant="sparkline" title="Calibration history sparkline" height={64} />
        </div>
      )}
      {(f.limitations ?? []).length > 0 && (
        <ul className="mt-2 list-disc pl-5 text-xs text-term-muted">
          {(f.limitations ?? []).map((l, i) => <li key={`${l}-${i}`}>{l}</li>)}
        </ul>
      )}
      <div className="mt-2 flex flex-wrap gap-2">
        <ProvenanceBadge p={f.provenance} />
      </div>
    </section>
  );
}

// Full Research/Explanation section: (A) deterministic + blended math +
// (B) AI opinion + audit link + verbatim disclosure + tier stubs.
function ResearchSection({
  symbol,
  forecast,
  aiOpinion = null,
  aiWeight,
  calibrationHistory = [],
  disclosure,
  onRequestAI,
  requestingAI = false,
  aiRequestError = null,
}) {
  const f = forecast ?? null;
  const w = clampAIWeight(aiWeight ?? f?.ai_weight ?? 0);
  const quantProb = typeof f?.quant_probability === "number" && Number.isFinite(f.quant_probability) ? f.quant_probability : (typeof f?.probability === "number" ? f.probability : null);
  const aiProb = typeof aiOpinion?.probability === "number" && Number.isFinite(aiOpinion.probability) ? aiOpinion.probability : (typeof f?.ai_probability === "number" ? f.ai_probability : null);
  const aiLabel = w > 0 ? sourceLabelForAIOpinion(aiOpinion, w) : AI_DISABLED_LABEL;
  const dirWord = f ? (f.probability >= 0.5 ? "bullish" : "bearish") : undefined;
  return (
    <section className="term-panel space-y-3 p-4" aria-label="research and explanation">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-bold tracking-widest">RESEARCH · EXPLANATION — NOT JUST A PREDICTION</h2>
        {symbol && <AuditLink symbol={symbol} />}
      </div>
      {f && <DeterministicEngineBlock forecast={f} calibrationHistory={calibrationHistory} />}
      {f && <BlendedForecastBar quantProb={quantProb} aiProb={aiProb} aiWeight={w} />}
      <div>
        <div className="mb-1 flex flex-wrap items-center gap-2">
          <p className="term-label">(B) AI Opinion</p>
          <SourceBadge source={aiLabel} />
        </div>
        <AIOpinionCard
          opinion={aiOpinion}
          deterministicProbability={quantProb ?? undefined}
          deterministicDirection={dirWord}
          deterministicEvidence={f?.evidence_ids ?? []}
          provenance={f?.provenance}
          aiWeight={w}
          onRequest={onRequestAI}
          requesting={requestingAI}
          requestError={aiRequestError}
        />
      </div>
      {/* Tier-gated stubs — always unlocked (locked=false), no enforcement. */}
      <div className="grid gap-2 md:grid-cols-2">
        <DeepResearchStub locked={false} tier="Free" feature="Deep Research" />
        <DeepResearchStub locked={false} tier="Free" feature="Report" />
      </div>
      <p className="rounded border border-term-amber bg-term-panel p-3 text-xs text-term-amber" role="note">
        {disclosure ?? f?.disclosure ?? "Not investment advice. Forecasts are measurable probabilities from the deterministic engine; AI opinions are bounded and capped at 20% influence."}
      </p>
    </section>
  );
}

export { SourceBadge, BlendedForecastBar, AuditLink, DeepResearchStub, DeterministicEngineBlock, ResearchSection };
export default ResearchSection;
