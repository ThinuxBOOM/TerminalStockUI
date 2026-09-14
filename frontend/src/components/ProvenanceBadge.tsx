import type { Provenance } from '../api/client';

function fmtTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

/**
 * Every displayed number must show source / timestamp / delay / quality.
 * Use next to any price, metric, or forecast figure.
 * Tolerates a missing envelope (renders an honest fallback, never throws).
 */
export default function ProvenanceBadge({ p }: { p: Provenance | null | undefined }) {
  if (!p || typeof p !== 'object') {
    return (
      <span
        className="inline-flex flex-wrap items-center gap-x-2 gap-y-0.5 rounded border border-term-border bg-term-bg px-2 py-1 text-[10px] text-term-muted"
        title="provenance=missing"
      >
        <span>
          src: <b className="text-term-text">unavailable</b>
        </span>
      </span>
    );
  }
  const missing = p.missing_fields ?? [];
  return (
    <span
      className="inline-flex flex-wrap items-center gap-x-2 gap-y-0.5 rounded border border-term-border bg-term-bg px-2 py-1 text-[10px] text-term-muted"
      title={`source=${p.source} as_of=${p.as_of} delay=${p.delay_minutes}m grade=${p.quality_grade} fallback=${p.fallback_used} missing=[${missing.join(',')}]`}
    >
      <span>
        src: <b className="text-term-text">{p.source}</b>
      </span>
      <span>as_of: {fmtTime(p.as_of)}</span>
      <span>delay: {p.delay_minutes}m</span>
      <span>
        Q:<b className="text-term-cyan">{p.quality_grade}</b>
      </span>
      {p.fallback_used && <span className="text-term-amber">fallback</span>}
      {missing.length > 0 && (
        <span className="text-term-red">missing: {missing.join(',')}</span>
      )}
    </span>
  );
}
