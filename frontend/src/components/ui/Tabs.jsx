import React from "react";

// Underline tabs. Items: [{id, label, badge?}]. Arrow keys move between tabs.
function Tabs({ tabs = [], active, onChange, ariaLabel = "Sections", className = "" }) {
  function onKey(e, i) {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    e.preventDefault();
    const next = tabs[(i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
    onChange?.(next.id);
    e.currentTarget.parentElement?.querySelectorAll('[role="tab"]')[tabs.indexOf(next)]?.focus();
  }
  return (
    <div role="tablist" aria-label={ariaLabel} className={`flex gap-1 overflow-x-auto border-b border-term-border ${className}`.trim()}>
      {tabs.map((t, i) => {
        const on = t.id === active;
        return (
          <button
            key={t.id}
            role="tab"
            type="button"
            aria-selected={on}
            tabIndex={on ? 0 : -1}
            onKeyDown={(e) => onKey(e, i)}
            onClick={() => onChange?.(t.id)}
            className={`-mb-px inline-flex shrink-0 items-center gap-1.5 border-b-2 px-3 py-2 text-sm transition-colors ${
              on ? "border-term-accent font-medium text-term-text" : "border-transparent text-term-muted hover:text-term-text"
            }`}
          >
            {t.label}
            {t.badge ? <span className="rounded bg-term-panel2 px-1 text-2xs text-term-muted">{t.badge}</span> : null}
          </button>
        );
      })}
    </div>
  );
}

export { Tabs, Tabs as default };
