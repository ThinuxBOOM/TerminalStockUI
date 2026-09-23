import React from "react";

function Tabs({ tabs = [], active, onChange, ariaLabel = "Tabs", className = "" }) {
  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      className={`flex flex-wrap gap-1 ${className}`.trim()}
    >
      {tabs.map((t) => {
        const id = typeof t === "string" ? t : t.id;
        const label = typeof t === "string" ? t : (t.label ?? t.id);
        const isActive = id === active;
        return (
          <button
            key={id}
            role="tab"
            type="button"
            aria-selected={isActive}
            onClick={() => onChange && onChange(id)}
            className={`rounded px-3 py-1.5 font-sans text-xs tracking-wide transition-colors duration-150 ${
              isActive
                ? "bg-term-greenDim text-term-green border-b-2 border-term-green font-semibold"
                : "text-term-muted hover:text-term-text"
            }`.trim()}
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}

export { Tabs, Tabs as default };
