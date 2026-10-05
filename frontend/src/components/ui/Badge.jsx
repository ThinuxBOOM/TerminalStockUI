import React from "react";

const TONES = {
  neutral: "border-term-border2 text-term-muted",
  accent: "border-term-accent/40 bg-term-accentDim text-term-accentHover",
  positive: "border-term-green/30 bg-term-greenDim text-term-green",
  negative: "border-term-red/30 bg-term-redDim text-term-red",
  warning: "border-term-amber/30 bg-term-amberDim text-term-amber",
};

function Badge({ tone = "neutral", children, title, className = "" }) {
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded border px-1.5 py-0.5 text-2xs font-semibold tracking-wide ${TONES[tone] ?? TONES.neutral} ${className}`.trim()}
    >
      {children}
    </span>
  );
}

export { Badge, Badge as default };
