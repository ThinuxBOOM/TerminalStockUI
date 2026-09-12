import { isFxProvenanceMissingError, isFreshFxProvenance, type Provenance } from '../api/client';

function fmtTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

/**
 * M7 FX provenance banner.
 * Shows source / as_of / delay / grade / fallback for the FX feed that
 * backs cross-market conversion. When provenance is missing or stale the
 * Watchlist must NOT render ranked numbers (see WatchlistPage gate).
 */
export default function FXProvenanceBanner({
  provenance,
  targetCcy,
  loading,
  error,
}: {
  provenance?: Provenance | null;
  targetCcy?: string;
  loading?: boolean;
  error?: unknown;
}) {
  const ccy = (targetCcy ?? '').trim().toUpperCase() || 'USD';

  if (loading) {
    return (
      <div className="term-panel p-3 text-xs text-term-muted" role="status">
        Loading FX provenance for target <b className="text-term-text">{ccy}</b>…
      </div>
    );
  }

  const gateError = error !== undefined && error !== null && isFxProvenanceMissingError(error);

  if (!provenance) {
    return (
      <div
        className="mb-3 rounded border border-term-red bg-term-panel p-3 text-xs text-term-red"
        role="alert"
        title={`fx_provenance=missing target=${ccy}`}
      >
        <b>FX provenance missing</b> · target {ccy}
        {gateError ? ' · backend gate: FX_PROVENANCE_MISSING' : ''}
        {' — '}
        Cross-market comparison unavailable — FX provenance missing.
      </div>
    );
  }

  const fresh = isFreshFxProvenance(provenance);
  const border = fresh ? 'border-term-border' : 'border-term-amber';
  const tone = fresh ? 'text-term-muted' : 'text-term-amber';

  return (
    <div
      className={`mb-3 rounded border ${border} bg-term-panel p-3 text-xs ${tone}`}
      role="status"
      title={`fx_source=${provenance.source} fx_as_of=${provenance.as_of} fx_delay=${provenance.delay_minutes}m fx_grade=${provenance.quality_grade} fx_fallback=${provenance.fallback_used} target=${ccy}`}
    >
      <span className="font-bold tracking-widest">
        FX PROVENANCE · TARGET {ccy}
      </span>
      <span className="ml-2">
        src: <b className="text-term-text">{provenance.source}</b>
      </span>
      <span className="ml-2">as_of: {fmtTime(provenance.as_of)}</span>
      <span className="ml-2">delay: {provenance.delay_minutes}m</span>
      <span className="ml-2">
        Q:<b className="text-term-cyan">{provenance.quality_grade}</b>
      </span>
      {provenance.fallback_used && <span className="ml-2 text-term-amber">fallback</span>}
      {provenance.missing_fields.length > 0 && (
        <span className="ml-2 text-term-red">missing: {provenance.missing_fields.join(',')}</span>
      )}
      {!fresh && (
        <span className="ml-2 font-bold">
          · stale — Cross-market comparison unavailable — FX provenance missing.
        </span>
      )}
    </div>
  );
}
