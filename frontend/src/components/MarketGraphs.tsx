import type { MarketBreadth, MarketSymbolRow } from '../api/markets';

const GREEN = '#3ddc84';
const RED = '#ff5c5c';
const MUTED = '#5b6b85';
const GRID = '#1c2433';
const AXIS = '#2a3448';

const compactFmt = new Intl.NumberFormat('en', {
  notation: 'compact',
  maximumFractionDigits: 1,
});

function compact(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—';
  try {
    return compactFmt.format(v);
  } catch {
    return String(v);
  }
}

function finite(v: number | null | undefined): v is number {
  return typeof v === 'number' && Number.isFinite(v);
}

/**
 * Cross-market comparison graphs: avg change % (diverging) + total volume
 * per MIC. SVG, no deps — same visual language as CalibrationChart.
 * Never ranks markets; both charts are independently scaled and labeled.
 */
export function CrossMarketChart({ markets }: { markets: MarketBreadth[] }) {
  const W = 360;
  const rowH = 26;
  const padL = 64;
  const padR = 56;
  const padT = 8;
  const H = padT + markets.length * rowH + 18;
  const maxAbs =
    Math.max(0.5, ...markets.map((m) => Math.abs(m.avg_change_pct ?? 0))) || 0.5;
  const maxVol =
    Math.max(1, ...markets.map((m) => m.total_volume ?? 0)) || 1;
  const cx = padL + (W - padL - padR) / 2;
  const half = (W - padL - padR) / 2;
  const x = (v: number) => cx + (Math.max(-maxAbs, Math.min(maxAbs, v)) / maxAbs) * half;

  return (
    <div className="grid min-w-0 gap-3 lg:grid-cols-2">
      <figure className="min-w-0">
        <figcaption className="term-label mb-1">Avg change % by market</figcaption>
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="w-full rounded border border-term-border bg-term-bg"
          role="img"
          aria-label={`average change percent per market: ${markets
            .map((m) => `${m.mic} ${m.avg_change_pct?.toFixed(2) ?? 'unavailable'}%`)
            .join(', ')}`}
        >
          <line x1={cx} y1={padT} x2={cx} y2={H - 18} stroke={AXIS} />
          {markets.map((m, i) => {
            const y = padT + i * rowH;
            const v = m.avg_change_pct;
            const has = finite(v);
            const bx = has ? Math.min(cx, x(v as number)) : cx;
            const bw = has ? Math.max(2, Math.abs(x(v as number) - cx)) : 0;
            const col = !has ? MUTED : (v as number) > 0 ? GREEN : (v as number) < 0 ? RED : MUTED;
            return (
              <g key={m.mic}>
                <text x={padL - 6} y={y + 14} fill={MUTED} fontSize={10} textAnchor="end">
                  {m.mic}
                </text>
                {has ? (
                  <rect x={bx} y={y + 4} width={bw} height={12} rx={2} fill={col} fillOpacity={0.8}>
                    <title>{`${m.label}: ${((v as number) > 0 ? '+' : '') + (v as number).toFixed(2)}%`}</title>
                  </rect>
                ) : null}
                <text x={W - padR + 6} y={y + 14} fill={MUTED} fontSize={10}>
                  {has ? `${((v as number) > 0 ? '+' : '') + (v as number).toFixed(2)}%` : '—'}
                </text>
              </g>
            );
          })}
          <text x={cx} y={H - 5} fill={MUTED} fontSize={9} textAnchor="middle">
            0 · ±{maxAbs.toFixed(1)}%
          </text>
        </svg>
      </figure>
      <figure className="min-w-0">
        <figcaption className="term-label mb-1">Total volume by market</figcaption>
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="w-full rounded border border-term-border bg-term-bg"
          role="img"
          aria-label={`total volume per market: ${markets
            .map((m) => `${m.mic} ${m.total_volume ?? 'unavailable'}`)
            .join(', ')}`}
        >
          {markets.map((m, i) => {
            const y = padT + i * rowH;
            const v = m.total_volume;
            const w =
              finite(v) && (v as number) > 0
                ? Math.max(2, ((v as number) / maxVol) * (W - padL - padR))
                : 0;
            return (
              <g key={m.mic}>
                <text x={padL - 6} y={y + 14} fill={MUTED} fontSize={10} textAnchor="end">
                  {m.mic}
                </text>
                {w > 0 ? (
                  <rect x={padL} y={y + 4} width={w} height={12} rx={2} fill="#3b82a0" fillOpacity={0.8}>
                    <title>{`${m.label}: ${compact(v)} shares`}</title>
                  </rect>
                ) : null}
                <text x={W - padR + 6} y={y + 14} fill={MUTED} fontSize={10}>
                  {compact(v)}
                </text>
              </g>
            );
          })}
          <text x={padL} y={H - 5} fill={MUTED} fontSize={9}>
            max {compact(maxVol)} shares
          </text>
        </svg>
      </figure>
    </div>
  );
}

/**
 * Per-market detail graphs: per-symbol change % (diverging) + volume bars.
 * Rows come from GET /api/markets/{mic}/liquidity (or the screener fallback).
 */
export function MarketDetailGraphs({ rows }: { rows: MarketSymbolRow[] }) {
  const W = 360;
  const rowH = 24;
  const padL = 92;
  const padR = 58;
  const padT = 8;
  const changes = rows.filter((r) => finite(r.change_pct));
  const vols = rows.filter((r) => finite(r.volume) && (r.volume as number) > 0);
  const sorted = [...rows].sort((a, b) => (b.change_pct ?? -Infinity) - (a.change_pct ?? -Infinity));
  const Hc = padT + Math.max(1, sorted.length) * rowH + 18;
  const Hv = padT + Math.max(1, rows.length) * rowH + 18;
  const maxAbs = Math.max(0.5, ...changes.map((r) => Math.abs(r.change_pct as number))) || 0.5;
  const maxVol = Math.max(1, ...vols.map((r) => r.volume as number)) || 1;
  const cx = padL + (W - padL - padR) / 2;
  const half = (W - padL - padR) / 2;
  const x = (v: number) => cx + (Math.max(-maxAbs, Math.min(maxAbs, v)) / maxAbs) * half;

  return (
    <div className="grid min-w-0 gap-3 lg:grid-cols-2">
      <figure className="min-w-0">
        <figcaption className="term-label mb-1">Change % by symbol</figcaption>
        {changes.length === 0 ? (
          <p className="rounded border border-term-border p-3 text-xs text-term-muted">
            No change data for these symbols yet.
          </p>
        ) : (
          <svg
            viewBox={`0 0 ${W} ${Hc}`}
            className="w-full rounded border border-term-border bg-term-bg"
            role="img"
            aria-label={`change percent by symbol: ${sorted
              .map((r) => `${r.symbol} ${r.change_pct?.toFixed(2) ?? 'unavailable'}%`)
              .join(', ')}`}
          >
            <line x1={cx} y1={padT} x2={cx} y2={Hc - 18} stroke={AXIS} />
            {sorted.map((r, i) => {
              const y = padT + i * rowH;
              const v = r.change_pct;
              const has = finite(v);
              const bx = has ? Math.min(cx, x(v as number)) : cx;
              const bw = has ? Math.max(2, Math.abs(x(v as number) - cx)) : 0;
              const col = !has ? MUTED : (v as number) > 0 ? GREEN : (v as number) < 0 ? RED : MUTED;
              return (
                <g key={r.symbol}>
                  <text x={padL - 6} y={y + 13} fill={MUTED} fontSize={9} textAnchor="end">
                    {r.symbol.length > 12 ? r.symbol.slice(0, 12) + '…' : r.symbol}
                  </text>
                  {has ? (
                    <rect x={bx} y={y + 3} width={bw} height={11} rx={2} fill={col} fillOpacity={0.8}>
                      <title>{`${r.symbol}: ${((v as number) > 0 ? '+' : '') + (v as number).toFixed(2)}%`}</title>
                    </rect>
                  ) : null}
                  <text x={W - padR + 6} y={y + 13} fill={MUTED} fontSize={9}>
                    {has ? `${((v as number) > 0 ? '+' : '') + (v as number).toFixed(2)}%` : '—'}
                  </text>
                </g>
              );
            })}
            <text x={cx} y={Hc - 5} fill={MUTED} fontSize={9} textAnchor="middle">
              0 · ±{maxAbs.toFixed(1)}%
            </text>
          </svg>
        )}
      </figure>
      <figure className="min-w-0">
        <figcaption className="term-label mb-1">Volume by symbol</figcaption>
        {vols.length === 0 ? (
          <p className="rounded border border-term-border p-3 text-xs text-term-muted">
            No volume data for these symbols yet — screener fallback carries no volume.
          </p>
        ) : (
          <svg
            viewBox={`0 0 ${W} ${Hv}`}
            className="w-full rounded border border-term-border bg-term-bg"
            role="img"
            aria-label={`volume by symbol: ${rows
              .map((r) => `${r.symbol} ${r.volume ?? 'unavailable'}`)
              .join(', ')}`}
          >
            {[0.5, 1].map((t) => (
              <line
                key={t}
                x1={padL + ((W - padL - padR) * t)}
                y1={padT}
                x2={padL + ((W - padL - padR) * t)}
                y2={Hv - 18}
                stroke={GRID}
                strokeWidth={1}
              />
            ))}
            {rows.map((r, i) => {
              const y = padT + i * rowH;
              const v = r.volume;
              const w =
                finite(v) && (v as number) > 0
                  ? Math.max(2, ((v as number) / maxVol) * (W - padL - padR))
                  : 0;
              return (
                <g key={r.symbol}>
                  <text x={padL - 6} y={y + 13} fill={MUTED} fontSize={9} textAnchor="end">
                    {r.symbol.length > 12 ? r.symbol.slice(0, 12) + '…' : r.symbol}
                  </text>
                  {w > 0 ? (
                    <rect x={padL} y={y + 3} width={w} height={11} rx={2} fill="#3b82a0" fillOpacity={0.8}>
                      <title>{`${r.symbol}: ${compact(v)} shares`}</title>
                    </rect>
                  ) : null}
                  <text x={W - padR + 6} y={y + 13} fill={MUTED} fontSize={9}>
                    {compact(v)}
                  </text>
                </g>
              );
            })}
            <text x={padL} y={Hv - 5} fill={MUTED} fontSize={9}>
              max {compact(maxVol)}
            </text>
          </svg>
        )}
      </figure>
    </div>
  );
}
