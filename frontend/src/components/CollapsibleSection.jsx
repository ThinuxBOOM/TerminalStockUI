import React, { useState } from "react";

function CollapsibleSection({ id, title, subtitle, defaultOpen = true, badge = null, children }) {
  const [open, setOpen] = useState(!!defaultOpen);
  return (
    <section className="term-panel min-w-0 p-4" aria-labelledby={id}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={`${id}-body`}
        className="flex w-full items-center justify-between gap-2 text-left"
      >
        <span className="min-w-0">
          <span id={id} className="term-label">
            {title}
          </span>
          {subtitle && <span className="mt-0.5 block text-[11px] font-normal normal-case tracking-normal text-term-muted">{subtitle}</span>}
        </span>
        <span className="flex shrink-0 items-center gap-2">
          {badge}
          <span className="term-btn-sm" aria-hidden="true">{open ? "▲ HIDE" : "▼ SHOW"}</span>
        </span>
      </button>
      {open && (
        <div id={`${id}-body`} className="mt-3">
          {children}
        </div>
      )}
    </section>
  );
}

export { CollapsibleSection as default };
