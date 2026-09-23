import React from "react";

// CSS-only tooltip (transform/opacity only — no layout animation, no new libs).
// Content appears on hover/focus-within; trigger stays keyboard accessible.
function Tooltip({ label, children, side = "top", className = "" }) {
  const pos =
    side === "right" ? "left-full top-1/2 ml-2 -translate-y-1/2"
    : side === "left" ? "right-full top-1/2 mr-2 -translate-y-1/2"
    : side === "bottom" ? "top-full left-1/2 mt-2 -translate-x-1/2"
    : "bottom-full left-1/2 mb-2 -translate-x-1/2";
  return (
    <span className={`group relative inline-flex ${className}`.trim()}>
      {children}
      <span
        role="tooltip"
        className={`pointer-events-none absolute z-50 whitespace-nowrap rounded border border-term-border2 bg-term-elevated px-2 py-1 text-2xs text-term-text opacity-0 shadow-overlay transition-opacity duration-150 group-hover:opacity-100 group-focus-within:opacity-100 ${pos}`.trim()}
      >
        {label}
      </span>
    </span>
  );
}

export { Tooltip, Tooltip as default };
