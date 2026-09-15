import React, { useMemo } from "react";

// Lightweight sparkline for calibration history (Brier/ECE trend or
// predicted-vs-observed dots). variant="sparkline" renders a compact strip
// for the Deterministic Engine block; default renders the full diagram.
function CalibrationSparkline({ rows, height = 64, title = "Calibration history sparkline" }) {
  const W = 360;
  const H = height;
  const safeRows = Array.isArray(rows) ? rows : [];
  const pts = useMemo(
    () =>
      safeRows
        .filter((r) => r && typeof r.mean_predicted === "number" && Number.isFinite(r.mean_predicted) && typeof r.fraction_positive === "number" && Number.isFinite(r.fraction_positive))
        .slice(0, 40),
    [safeRows]
  );
  if (pts.length === 0) {
    return (
      <div role="status">
        {title && <p className="term-label mb-1">{title}</p>}
        <p className="text-xs text-term-muted">No calibration bins yet — run the Backtest Lab.</p>
      </div>
    );
  }
  const step = pts.length > 1 ? W / (pts.length - 1) : W;
  const y = (p) => 6 + (1 - Math.min(1, Math.max(0, p))) * (H - 12);
  const line = pts.map((r, i) => `${i === 0 ? "M" : "L"}${(i * step).toFixed(1)},${y(r.fraction_positive).toFixed(1)}`).join(" ");
  return (
    <figure>
      {title && <figcaption className="term-label mb-1">{title}</figcaption>}
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full rounded border border-term-border bg-term-bg" role="img" aria-label="calibration history sparkline">
        <path d={line} fill="none" stroke="#3ddc84" strokeWidth="1.5" />
        {pts.map((r, i) => (
          <circle key={i} cx={i * step} cy={y(r.fraction_positive)} r={2} fill="#3ddc84" fillOpacity={0.8}>
            <title>{`pred=${Number(r.mean_predicted).toFixed(3)} obs=${Number(r.fraction_positive).toFixed(3)} n=${r.count}`}</title>
          </circle>
        ))}
      </svg>
    </figure>
  );
}

function CalibrationChart({ rows, height = 190, title = "Calibration", variant }) {
  if (variant === "sparkline") {
    return <CalibrationSparkline rows={rows} height={height} title={title} />;
  }
  const W = 360;
  const H = height;
  const padL = 30;
  const padR = 10;
  const padT = 10;
  const padB = 22;
  const iw = W - padL - padR;
  const ih = H - padT - padB;
  const x = (p) => padL + Math.min(1, Math.max(0, p)) * iw;
  const y = (p) => padT + (1 - Math.min(1, Math.max(0, p))) * ih;
  const finite = (v) => typeof v === "number" && Number.isFinite(v);
  const safeRows = rows ?? [];
  const plotted = useMemo(
    () => safeRows.filter((r) => r && finite(r.mean_predicted) && finite(r.fraction_positive)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [safeRows]
  );
  const maxCount = useMemo(
    () => Math.max(1, ...safeRows.map((r) => (typeof r?.count === "number" && Number.isFinite(r.count) ? r.count : 0))),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [safeRows]
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
          <line x1={padL} y1={padT} x2={padL} y2={H - padB} stroke="#2a3448" />
          <line x1={padL} y1={H - padB} x2={W - padR} y2={H - padB} stroke="#2a3448" />
          <line x1={x(0)} y1={y(0)} x2={x(1)} y2={y(1)} stroke="#3b82a0" strokeDasharray="4 3" strokeWidth={1} />
          <text x={W - padR} y={padT + 2} fill="#5b6b85" fontSize={9} textAnchor="end">perfect</text>
          {[0.25, 0.5, 0.75].map((t) => (
            <g key={t}>
              <line x1={x(t)} y1={padT} x2={x(t)} y2={H - padB} stroke="#1c2433" strokeWidth={1} />
              <line x1={padL} y1={y(t)} x2={W - padR} y2={y(t)} stroke="#1c2433" strokeWidth={1} />
              <text x={x(t)} y={H - 8} fill="#5b6b85" fontSize={9} textAnchor="middle">{t.toFixed(2)}</text>
              <text x={padL - 4} y={y(t) + 3} fill="#5b6b85" fontSize={9} textAnchor="end">{t.toFixed(2)}</text>
            </g>
          ))}
          {safeRows.map((r, i) => {
            if (!r || (finite(r.mean_predicted) && finite(r.fraction_positive))) return null;
            if (!finite(r.bin_low) || !finite(r.bin_high)) return null;
            const mid = (r.bin_low + r.bin_high) / 2;
            return <line key={`e${i}`} x1={x(mid)} y1={H - padB} x2={x(mid)} y2={H - padB + 5} stroke="#ff5c5c" strokeWidth={2} />;
          })}
          {plotted.map((r, i) => (
            <circle
              key={i}
              cx={x(r.mean_predicted)}
              cy={y(r.fraction_positive)}
              r={3 + 7 * Math.sqrt(r.count / maxCount)}
              fill="#3ddc84"
              fillOpacity={0.75}
              stroke="#0f141d"
              strokeWidth={1}
            >
              <title>{`bin ${finite(r.bin_low) ? r.bin_low.toFixed(2) : "—"}–${finite(r.bin_high) ? r.bin_high.toFixed(2) : "—"} · n=${r.count} · pred=${r.mean_predicted.toFixed(3)} · obs=${r.fraction_positive.toFixed(3)}`}</title>
            </circle>
          ))}
          <text x={padL} y={H - 8} fill="#5b6b85" fontSize={9}>0</text>
          <text x={x(1)} y={H - 8} fill="#5b6b85" fontSize={9} textAnchor="middle">1 · predicted →</text>
        </svg>
      )}
      <p className="mt-1 text-[10px] text-term-muted">
        Dots above the diagonal = under-confident; below = over-confident. Size ∝ bin count. Red ticks = empty bins.
      </p>
    </figure>
  );
}

export { CalibrationSparkline, CalibrationChart as default };
