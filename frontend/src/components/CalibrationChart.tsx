import { useMemo } from 'react';
import type { ReliabilityRow } from '../api/client';

type Props = {
  rows: ReliabilityRow[];
  height?: number;
  title?: string;
};

/**
 * Reliability diagram (SVG, no new deps): x = mean predicted probability,
 * y = observed fraction positive. Dashed diagonal = perfect calibration.
 * Dot area scales with bin count; empty bins render as ticks on the x-axis.
 */
export default function CalibrationChart({ rows, height = 190, title = 'Calibration' }: Props) {
  const W = 360;
  const H = height;
  const padL = 30;
  const padR = 10;
  const padT = 10;
  const padB = 22;
  const iw = W - padL - padR;
  const ih = H - padT - padB;
  const x = (p: number) => padL + Math.min(1, Math.max(0, p)) * iw;
  const y = (p: number) => padT + (1 - Math.min(1, Math.max(0, p))) * ih;

  const finite = (v: number | null | undefined): v is number =>
    typeof v === 'number' && Number.isFinite(v);
  const safeRows = rows ?? [];
  const plotted = useMemo(
    () => safeRows.filter((r) => r && finite(r.mean_predicted) && finite(r.fraction_positive)),
    [safeRows],
  );
  const maxCount = useMemo(
    () => Math.max(1, ...safeRows.map((r) => (typeof r?.count === 'number' && Number.isFinite(r.count) ? r.count : 0))),
    [safeRows],
  );

  return (
    <figure>
      {title && <figcaption className="term-label mb-1">{title}</figcaption>}
      {safeRows.length === 0 ? (
        <p className="text-xs text-term-muted" role="status">No calibration bins yet — run the Backtest Lab.</p>
      ) : (
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="w-full rounded border border-term-border bg-term-bg"
          role="img"
          aria-label="reliability diagram: predicted vs observed probability"
        >
          {/* axes */}
          <line x1={padL} y1={padT} x2={padL} y2={H - padB} stroke="#2a3448" />
          <line x1={padL} y1={H - padB} x2={W - padR} y2={H - padB} stroke="#2a3448" />
          {/* perfect-calibration diagonal */}
          <line
            x1={x(0)}
            y1={y(0)}
            x2={x(1)}
            y2={y(1)}
            stroke="#3b82a0"
            strokeDasharray="4 3"
            strokeWidth={1}
          />
          <text x={W - padR} y={padT + 2} fill="#5b6b85" fontSize={9} textAnchor="end">
            perfect
          </text>
          {/* gridlines */}
          {[0.25, 0.5, 0.75].map((t) => (
            <g key={t}>
              <line x1={x(t)} y1={padT} x2={x(t)} y2={H - padB} stroke="#1c2433" strokeWidth={1} />
              <line x1={padL} y1={y(t)} x2={W - padR} y2={y(t)} stroke="#1c2433" strokeWidth={1} />
              <text x={x(t)} y={H - 8} fill="#5b6b85" fontSize={9} textAnchor="middle">
                {t.toFixed(2)}
              </text>
              <text x={padL - 4} y={y(t) + 3} fill="#5b6b85" fontSize={9} textAnchor="end">
                {t.toFixed(2)}
              </text>
            </g>
          ))}
          {/* empty bins as ticks */}
          {safeRows.map((r, i) => {
            if (!r || finite(r.mean_predicted) && finite(r.fraction_positive)) return null;
            if (!finite(r.bin_low) || !finite(r.bin_high)) return null;
            const mid = (r.bin_low + r.bin_high) / 2;
            return (
              <line
                key={`e${i}`}
                x1={x(mid)}
                y1={H - padB}
                x2={x(mid)}
                y2={H - padB + 5}
                stroke="#ff5c5c"
                strokeWidth={2}
              />
            );
          })}
          {/* bins */}
          {plotted.map((r, i) => (
            <circle
              key={i}
              cx={x(r.mean_predicted as number)}
              cy={y(r.fraction_positive as number)}
              r={3 + 7 * Math.sqrt(r.count / maxCount)}
              fill="#3ddc84"
              fillOpacity={0.75}
              stroke="#0f141d"
              strokeWidth={1}
            >
              <title>
                {`bin ${finite(r.bin_low) ? r.bin_low.toFixed(2) : '—'}–${finite(r.bin_high) ? r.bin_high.toFixed(2) : '—'} · n=${r.count} · pred=${(r.mean_predicted as number).toFixed(3)} · obs=${(r.fraction_positive as number).toFixed(3)}`}
              </title>
            </circle>
          ))}
          <text x={padL} y={H - 8} fill="#5b6b85" fontSize={9}>
            0
          </text>
          <text x={x(1)} y={H - 8} fill="#5b6b85" fontSize={9} textAnchor="middle">
            1 · predicted →
          </text>
        </svg>
      )}
      <p className="mt-1 text-[10px] text-term-muted">
        Dots above the diagonal = under-confident; below = over-confident. Size ∝ bin count. Red
        ticks = empty bins.
      </p>
    </figure>
  );
}
