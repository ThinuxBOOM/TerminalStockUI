import React from "react";

// Compact single-choice control (horizons, timeframes). options: [{value, label}].
function Segmented({ options = [], value, onChange, ariaLabel, size = "sm", className = "" }) {
  const pad = size === "xs" ? "px-2 py-0.5 text-2xs" : "px-2.5 py-1 text-xs";
  return (
    <div role="radiogroup" aria-label={ariaLabel} className={`inline-flex rounded-md border border-term-border bg-term-input p-0.5 ${className}`.trim()}>
      {options.map((o) => {
        const on = o.value === value;
        return (
          <button
            key={String(o.value)}
            type="button"
            role="radio"
            aria-checked={on}
            title={o.title}
            onClick={() => onChange?.(o.value)}
            className={`rounded ${pad} font-medium tabular-nums transition-colors ${on ? "bg-term-elevated text-term-text shadow-panel" : "text-term-muted hover:text-term-text"}`}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

export { Segmented, Segmented as default };
