import React from "react";
import ProvenanceBadge from "./ProvenanceBadge";
import { AI_DISABLED_LABEL, DISAGREE_TOL, sourceLabelForAIOpinion } from "../api/client";
import { formatPct1 } from "../utils/format";

const DISAGREE_TOL_EXPORT = DISAGREE_TOL;

function SourceBadgeInline({ source }) {
  const isDisabled = String(source ?? "").toUpperCase().startsWith("AI DISABLED");
  return (
    <span
      className={`rounded border px-2 py-0.5 text-[10px] font-bold tracking-widest ${isDisabled ? "border-term-border text-term-muted" : "border-term-amber text-term-amber"}`}
    >
      {source}
    </span>
  );
}

function AIOpinionCard({
  opinion,
  deterministicProbability,
  deterministicDirection,
  deterministicEvidence = [],
  provenance,
  aiWeight = 0,
  onRequest,
  requesting,
  requestError,
}) {
  const w = typeof aiWeight === "number" && Number.isFinite(aiWeight) ? aiWeight : 0;
  const disabled = !(w > 0);

  // Safe degrade: malformed opinion objects never throw into the render path.
  const safe = opinion && typeof opinion === "object" ? opinion : null;

  if (!safe) {
    return (
      <section className="term-panel border-l-2 border-term-cyan p-4" aria-label="ai opinion">
        <div className="flex flex-wrap items-center gap-2">
          <p className="term-label">AI opinion · bounded (capped 20%)</p>
          <SourceBadgeInline source={AI_DISABLED_LABEL} />
        </div>
        <p className="mt-1 text-sm text-term-muted">
          No AI opinion attached. AI runs only on explicit request and can at most nudge — never override — the deterministic forecast.
        </p>
        {onRequest && (
          <button className="term-btn mt-3" type="button" disabled={requesting} onClick={onRequest}>
            {requesting ? "REQUESTING…" : "REQUEST AI OPINION"}
          </button>
        )}
        {requestError && <p className="mt-2 text-xs text-term-red">{requestError}</p>}
      </section>
    );
  }

  const prob = typeof safe.probability === "number" && Number.isFinite(safe.probability) ? safe.probability : null;
  if (prob === null) {
    // Malformed AI probability -> safe degrade to the disabled/empty state,
    // deterministic core above is unaffected.
    return (
      <section className="term-panel border-l-2 border-term-cyan p-4" aria-label="ai opinion">
        <div className="flex flex-wrap items-center gap-2">
          <p className="term-label">AI opinion · bounded</p>
          <SourceBadgeInline source={AI_DISABLED_LABEL} />
        </div>
        <p className="mt-1 text-sm text-term-muted" role="status">
          AI opinion degraded — malformed probability, discarded. Deterministic forecast unaffected.
        </p>
        {requestError && <p className="mt-2 text-xs text-term-red">{requestError}</p>}
      </section>
    );
  }

  const gap =
    typeof deterministicProbability === "number" && Number.isFinite(deterministicProbability) && prob !== null
      ? Math.abs(prob - deterministicProbability)
      : null;
  const opinionDir = String(safe.direction ?? "").toLowerCase();
  const detDir = String(deterministicDirection ?? "").toLowerCase();
  const dirMismatch = detDir !== "" && opinionDir !== "" && opinionDir !== detDir;
  const disagree = (gap !== null && gap > DISAGREE_TOL) || dirMismatch;
  const evidence = Array.isArray(safe.evidence_ids) ? safe.evidence_ids : [];
  const catalysts = Array.isArray(safe.catalysts) ? safe.catalysts : [];
  const risks = Array.isArray(safe.risks) ? safe.risks : [];
  const limitations = Array.isArray(safe.limitations) ? safe.limitations : [];
  const noEvidence = evidence.length === 0;
  const prov = safe.provenance ?? provenance ?? null;
  const provider = safe.provider ? String(safe.provider) : null;
  const sourceLabel = disabled ? AI_DISABLED_LABEL : sourceLabelForAIOpinion(safe, w);
  const detEvidence = Array.isArray(deterministicEvidence) ? deterministicEvidence : [];
  const latency = typeof safe.latency_ms === "number" && Number.isFinite(safe.latency_ms) ? safe.latency_ms : null;
  const tokens =
    typeof safe.tokens === "number" && Number.isFinite(safe.tokens)
      ? safe.tokens
      : typeof safe.usage?.total_tokens === "number"
        ? safe.usage.total_tokens
        : null;

  return (
    <section className="term-panel border-l-2 border-term-cyan p-4" aria-label="ai opinion">
      <div className="flex flex-wrap items-center gap-2">
        <p className="term-label">AI opinion · bounded</p>
        <SourceBadgeInline source={sourceLabel} />
        <span
          className="rounded border border-term-amber px-2 py-0.5 text-[10px] font-bold tracking-widest text-term-amber"
          title="Fixed v1 policy: AI weight capped at 20%, not user-adjustable"
        >
          CAPPED 20%
        </span>
        {safe.stub === true && (
          <span
            className="rounded border border-term-red px-2 py-0.5 text-[10px] font-bold tracking-widest text-term-red"
            title={limitations[0] ?? "No live model call was made"}
          >
            STUB — NO LIVE CALL
          </span>
        )}
        {provider && (
          <span className="text-[11px] text-term-muted">
            {provider}{safe.model ? ` · ${safe.model}` : ""}
          </span>
        )}
      </div>
      <p className="mt-2 text-sm">
        Direction: <b className="text-term-text">{safe.direction ?? "—"}</b> · horizon{" "}
        <b className="text-term-text">{safe.time_horizon_days}d</b>
        {prov && (
          <span className="ml-2">
            <ProvenanceBadge p={prov} />
          </span>
        )}
      </p>
      <p className="term-num mt-1 text-2xl font-bold text-term-text">
        {formatPct1(prob)}
        <span className="ml-2 align-middle text-[10px] font-normal text-term-muted">
          AI-only figure — blended forecast moves at most 20% of the way toward it
        </span>
      </p>
      {disabled && (
        <p className="mt-1 text-[11px] text-term-muted" role="status">
          AI weight is 0 — this opinion does not affect the blended forecast.
        </p>
      )}
      {disagree && (
        <div className="mt-2 rounded border border-term-amber bg-term-panel p-2 text-xs text-term-amber" role="alert">
          ⚠ Disagreement with deterministic core{gap !== null ? ` (Δ ${(gap * 100).toFixed(1)}pp)` : ""}{dirMismatch ? " · direction mismatch" : ""} — Δ&gt;15pp — treat blended confidence as lower.
        </div>
      )}
      {noEvidence && (
        <div className="mt-2 rounded border border-term-red p-2 text-xs text-term-red" role="alert">
          ✕ Rejected-grade opinion: no evidence_ids supplied. Per policy, claims without evidence IDs are discarded — this card is shown for transparency only.
        </div>
      )}
      <div className="mt-2 grid gap-2 text-xs md:grid-cols-2">
        <div className="rounded border border-term-border p-2">
          <p className="font-bold text-term-green">Catalysts</p>
          {catalysts.length === 0 ? (
            <p className="text-term-muted">—</p>
          ) : (
            <ul className="list-disc pl-4 text-term-muted">{catalysts.map((c, i) => <li key={`${c}-${i}`}>{c}</li>)}</ul>
          )}
        </div>
        <div className="rounded border border-term-border p-2">
          <p className="font-bold text-term-red">Risks</p>
          {risks.length === 0 ? (
            <p className="text-term-muted">—</p>
          ) : (
            <ul className="list-disc pl-4 text-term-muted">{risks.map((c, i) => <li key={`${c}-${i}`}>{c}</li>)}</ul>
          )}
        </div>
      </div>
      <div className="mt-2 text-xs">
        <p className="text-term-muted">
          Evidence:{" "}
          {evidence.length === 0 ? (
            <span className="text-term-red">none supplied</span>
          ) : (
            evidence.map((e, i) => {
              const linked = detEvidence.includes(e);
              return (
                <code
                  key={`${e}-${i}`}
                  className="mr-1 rounded bg-term-bg px-1 py-0.5 text-term-cyan"
                  title={linked ? "links to deterministic evidence" : "AI-cited evidence id"}
                >
                  {e}{linked ? " ✓" : ""}
                </code>
              );
            })
          )}
        </p>
        {detEvidence.length > 0 && evidence.length > 0 && (
          <p className="mt-0.5 text-[10px] text-term-muted">✓ = links to deterministic evidence_ids.</p>
        )}
        {limitations.length > 0 && (
          <ul className="mt-1 list-disc pl-5 text-term-muted">
            {limitations.map((l, i) => <li key={`${l}-${i}`}>{l}</li>)}
          </ul>
        )}
        {(latency !== null || tokens !== null) && (
          <p className="mt-1 text-[10px] text-term-muted">
            {latency !== null && <span>latency {Math.round(latency)}ms</span>}
            {latency !== null && tokens !== null && <span> · </span>}
            {tokens !== null && <span>{tokens} tokens</span>}
          </p>
        )}
      </div>
    </section>
  );
}

export { DISAGREE_TOL_EXPORT as DISAGREE_TOL };
export default AIOpinionCard;
