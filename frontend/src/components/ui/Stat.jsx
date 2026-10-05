import React from "react";

// Stat tile: label, value, optional signed delta and a one-line note.
// `tone` colors the delta only (direction x whether up is good).
function Stat({ label, value, delta, tone = "neutral", note, hint, size = "md", className = "" }) {
  const toneCls = tone === "positive" ? "text-term-green" : tone === "negative" ? "text-term-red" : tone === "warning" ? "text-term-amber" : "text-term-muted";
  const valueCls = size === "lg" ? "text-2xl" : size === "sm" ? "text-sm" : "text-lg";
  return (
    <div className={`min-w-0 ${className}`.trim()} title={hint || undefined}>
      <div className="text-2xs font-medium uppercase leading-tight tracking-[0.08em] text-term-muted">{label}</div>
      <div className={`mt-0.5 truncate font-semibold tracking-tight text-term-text ${valueCls}`}>{value ?? "—"}</div>
      {delta !== undefined && delta !== null && delta !== "" ? (
        <div className={`term-num mt-0.5 truncate text-xs ${toneCls}`}>{delta}</div>
      ) : null}
      {note ? <div className="mt-0.5 text-2xs leading-snug text-term-faint">{note}</div> : null}
    </div>
  );
}

export { Stat, Stat as default };
