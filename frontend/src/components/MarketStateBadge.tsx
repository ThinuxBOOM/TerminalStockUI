import {
  deriveMarketState,
  type MarketState,
  type Provenance,
} from '../api/client';

const STYLE: Record<MarketState, string> = {
  open: 'border-term-green text-term-green',
  closed: 'border-term-border text-term-muted',
  lunch: 'border-term-amber text-term-amber',
  delayed: 'border-term-amber text-term-amber',
  stale: 'border-term-red text-term-red',
};

const LABEL: Record<MarketState, string> = {
  open: 'MARKET OPEN',
  closed: 'MARKET CLOSED',
  lunch: 'LUNCH BREAK',
  delayed: 'DELAYED',
  stale: 'STALE',
};

/**
 * M6 market-state badge (SSE-aware: includes `lunch` for the XSHG midday break).
 * Explicit API `market_state` wins; otherwise derived from provenance
 * (open|delayed|stale only — closed/lunch are never guessed).
 */
export default function MarketStateBadge({
  state,
  provenance,
}: {
  state?: unknown;
  provenance?: Provenance;
}) {
  const fallback: Provenance = provenance ?? {
    source: 'unknown',
    as_of: new Date(0).toISOString(),
    delay_minutes: -1,
    quality_grade: 'U',
    fallback_used: true,
    missing_fields: ['market_state'],
  };
  const resolved: MarketState = deriveMarketState(fallback, state);
  const explicit =
    typeof state === 'string' && state.trim() !== '' ? state : '(derived from provenance)';
  return (
    <span
      className={`rounded border px-2 py-0.5 text-[10px] font-bold tracking-widest ${STYLE[resolved]}`}
      title={`market_state=${resolved} explicit=${String(explicit)}`}
    >
      {LABEL[resolved]}
    </span>
  );
}
