import React from "react";

// Signed change with direction arrow; color + arrow (never color alone).
function Delta({ value, digits = 2, suffix = "%", scale = 100, className = "" }) {
  if (typeof value !== "number" || !Number.isFinite(value)) return <span className={`text-term-faint ${className}`}>—</span>;
  const v = value * scale;
  const up = v > 0;
  const flat = Math.abs(v) < 10 ** -digits / 2;
  const cls = flat ? "text-term-muted" : up ? "text-term-green" : "text-term-red";
  const arrow = flat ? "" : up ? "▲ " : "▼ ";
  return (
    <span className={`term-num ${cls} ${className}`.trim()}>
      {arrow}
      {up ? "+" : ""}
      {v.toFixed(digits)}
      {suffix}
    </span>
  );
}

export { Delta, Delta as default };
