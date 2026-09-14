import type { AIOpinion, Provenance } from '../api/client';
import ProvenanceBadge from './ProvenanceBadge';

type Props = {
  opinion: AIOpinion | null;
  deterministicProbability?: number;
  deterministicDirection?: string;
  provenance?: Provenance;
  onRequest?: () => void;
  requesting?: boolean;
  requestError?: string | null;
};

const DISAGREE_TOL = 0.15;

/**
 * Bounded AI opinion card (spec M5). AI never overrides the deterministic
 * core: influence capped at 20%, claims require evidence IDs, disagreement
 * lowers confidence and raises a visible warning.
 */
export default function AIOpinionCard({
  opinion,
  deterministicProbability,
  deterministicDirection,
  provenance,
  onRequest,
  requesting,
  requestError,
}: Props) {
  if (!opinion) {
    return (
      <section className="term-panel p-4">
        <p className="term-label">AI opinion · bounded (capped 20%)</p>
        <p className="mt-1 text-sm text-term-muted">
          No AI opinion attached. AI runs only on explicit request and can at most nudge —
          never override — the deterministic forecast.
        </p>
        {onRequest && (
          <button className="term-btn mt-3" type="button" disabled={requesting} onClick={onRequest}>
            {requesting ? 'REQUESTING…' : 'REQUEST AI OPINION'}
          </button>
        )}
        {requestError && <p className="mt-2 text-xs text-term-red">{requestError}</p>}
      </section>
    );
  }

  const prob = typeof opinion.probability === 'number' && Number.isFinite(opinion.probability)
    ? opinion.probability
    : null;
  const gap =
    typeof deterministicProbability === 'number' &&
    Number.isFinite(deterministicProbability) &&
    prob !== null
      ? Math.abs(prob - deterministicProbability)
      : null;
  const opinionDir = String(opinion.direction ?? '').toLowerCase();
  const detDir = String(deterministicDirection ?? '').toLowerCase();
  const dirMismatch =
    detDir !== '' && opinionDir !== '' && opinionDir !== detDir;
  const disagree = (gap !== null && gap > DISAGREE_TOL) || dirMismatch;
  const evidence = opinion.evidence_ids ?? [];
  const catalysts = opinion.catalysts ?? [];
  const risks = opinion.risks ?? [];
  const limitations = opinion.limitations ?? [];
  const noEvidence = evidence.length === 0;
  const prov = opinion.provenance ?? provenance ?? null;

  return (
    <section className="term-panel p-4">
      <div className="flex flex-wrap items-center gap-2">
        <p className="term-label">AI opinion · bounded</p>
        <span
          className="rounded border border-term-amber px-2 py-0.5 text-[10px] font-bold tracking-widest text-term-amber"
          title="Fixed v1 policy: AI weight capped at 20%, not user-adjustable"
        >
          CAPPED 20%
        </span>
        {(opinion as { stub?: unknown }).stub === true && (
          <span
            className="rounded border border-term-red px-2 py-0.5 text-[10px] font-bold tracking-widest text-term-red"
            title={limitations[0] ?? 'No live model call was made'}
          >
            STUB — NO LIVE CALL
          </span>
        )}
        {opinion.provider && (
          <span className="text-[11px] text-term-muted">
            {opinion.provider}
            {opinion.model ? ` · ${opinion.model}` : ''}
          </span>
        )}
      </div>

      <p className="mt-2 text-sm">
        Direction: <b className="text-term-text">{opinion.direction ?? '—'}</b> · horizon{' '}
        <b className="text-term-text">{opinion.time_horizon_days}d</b>
        {prov && (
          <span className="ml-2">
            <ProvenanceBadge p={prov} />
          </span>
        )}
      </p>
      <p className="mt-1 text-2xl font-bold">
        {prob !== null ? `${(prob * 100).toFixed(1)}%` : 'unavailable'}
        <span className="ml-2 align-middle text-[10px] font-normal text-term-muted">
          AI-only figure — blended forecast moves at most 20% of the way toward it
        </span>
      </p>

      {disagree && (
        <div
          className="mt-2 rounded border border-term-amber bg-term-panel p-2 text-xs text-term-amber"
          role="alert"
        >
          ⚠ Disagreement with deterministic core
          {gap !== null ? ` (Δ ${(gap * 100).toFixed(1)}pp)` : ''}
          {dirMismatch ? ' · direction mismatch' : ''} — treat blended confidence as lower.
        </div>
      )}
      {noEvidence && (
        <div className="mt-2 rounded border border-term-red p-2 text-xs text-term-red" role="alert">
          ✕ Rejected-grade opinion: no evidence_ids supplied. Per policy, claims without evidence
          IDs are discarded — this card is shown for transparency only.
        </div>
      )}

      <div className="mt-2 grid gap-2 text-xs md:grid-cols-2">
        <div className="rounded border border-term-border p-2">
          <p className="font-bold text-term-green">Catalysts</p>
          {catalysts.length === 0 ? (
            <p className="text-term-muted">—</p>
          ) : (
            <ul className="list-disc pl-4 text-term-muted">
              {catalysts.map((c, i) => (
                <li key={`${c}-${i}`}>{c}</li>
              ))}
            </ul>
          )}
        </div>
        <div className="rounded border border-term-border p-2">
          <p className="font-bold text-term-red">Risks</p>
          {risks.length === 0 ? (
            <p className="text-term-muted">—</p>
          ) : (
            <ul className="list-disc pl-4 text-term-muted">
              {risks.map((c, i) => (
                <li key={`${c}-${i}`}>{c}</li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <div className="mt-2 text-xs">
        <p className="text-term-muted">
          Evidence:{' '}
          {evidence.length === 0 ? (
            <span className="text-term-red">none supplied</span>
          ) : (
            evidence.map((e, i) => (
              <code key={`${e}-${i}`} className="mr-1 rounded bg-term-bg px-1 py-0.5 text-term-cyan">
                {e}
              </code>
            ))
          )}
        </p>
        {limitations.length > 0 && (
          <ul className="mt-1 list-disc pl-5 text-term-muted">
            {limitations.map((l, i) => (
              <li key={`${l}-${i}`}>{l}</li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
