import React from "react";

// Semantic connection/data dot. Never color-alone: always pairs dot + label.
const DOT = {
  positive: "bg-term-green",
  negative: "bg-term-red",
  warning: "bg-term-amber",
  neutral: "bg-term-muted",
  info: "bg-term-cyan",
  fresh: "bg-term-green",
  stale: "bg-term-amber",
  error: "bg-term-red",
};

function StatusDot({ state = "neutral", label, pulse = false, className = "", dotClassName = "" }) {
  const dot = DOT[state] ?? DOT.neutral;
  return (
    <span
      className={`inline-flex items-center gap-1.5 text-2xs font-semibold tracking-wide text-term-muted ${className}`.trim()}
      role="status"
    >
      <span
        aria-hidden="true"
        className={`h-1.5 w-1.5 rounded-full ${dot} ${pulse ? "animate-pulse" : ""} ${dotClassName}`.trim()}
      />
      {label ? <span>{label}</span> : null}
    </span>
  );
}

export { StatusDot, StatusDot as default };
