import React from "react";

// Compact terminal metric: label + tabular-nums value + optional delta.
function Metric({ label, value, delta, deltaTone, hint, className = "" }) {
  const tone =
    deltaTone === "positive" ? "text-term-green"
    : deltaTone === "negative" ? "text-term-red"
    : deltaTone === "warning" ? "text-term-amber"
    : "text-term-muted";
  return (
    <div className={`min-w-0 ${className}`.trim()} title={hint ?? undefined}>
      <div className="term-label truncate">{label}</div>
      <div className="term-num truncate text-lg font-bold text-term-text">{value}</div>
      {delta !== undefined && delta !== null && delta !== "" ? (
        <div className={`term-num truncate text-xs ${tone}`}>{delta}</div>
      ) : null}
    </div>
  );
}

export { Metric, Metric as default };
