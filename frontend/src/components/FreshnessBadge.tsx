import { freshnessOf, type Provenance } from '../api/client';

const STYLE: Record<string, string> = {
  live: 'border-term-green text-term-green',
  delayed: 'border-term-amber text-term-amber',
  stale: 'border-term-red text-term-red',
  cached: 'border-term-amber text-term-amber',
};

export default function FreshnessBadge({ p }: { p: Provenance }) {
  const f = freshnessOf(p);
  const label =
    f === 'live' ? 'LIVE' : f === 'delayed' ? `DELAYED ${p.delay_minutes}m` : f === 'cached' ? 'CACHED / FALLBACK' : 'STALE';
  return (
    <span className={`rounded border px-2 py-0.5 text-[10px] font-bold tracking-widest ${STYLE[f]}`}>
      {label}
    </span>
  );
}
