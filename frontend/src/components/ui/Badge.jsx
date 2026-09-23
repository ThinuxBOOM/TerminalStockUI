import React from "react";

// Semantic states: positive / negative / warning / neutral / info / fresh / stale / error.
const TONES = {
  positive: "border-term-green/40 bg-term-greenDim text-term-green",
  negative: "border-term-red/40 bg-term-redDim text-term-red",
  warning: "border-term-amber/40 bg-term-amberDim text-term-amber",
  neutral: "border-term-border2 bg-term-panel2 text-term-muted",
  info: "border-term-cyan/40 bg-term-panel2 text-term-cyan",
  fresh: "border-term-green/40 bg-term-greenDim text-term-green",
  stale: "border-term-amber/40 bg-term-amberDim text-term-amber",
  error: "border-term-red/40 bg-term-redDim text-term-red",
};

function Badge({ tone = "neutral", className = "", children, ...rest }) {
  const t = TONES[tone] ?? TONES.neutral;
  return (
    <span
      className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 font-sans text-2xs font-semibold tracking-wide ${t} ${className}`.trim()}
      {...rest}
    >
      {children}
    </span>
  );
}

export { Badge, Badge as default };
