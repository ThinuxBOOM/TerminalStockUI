import React from "react";

// Surface for a block of content: optional title row with a subtitle and
// right-aligned actions. `pad={false}` for edge-to-edge tables.
function Card({ title, subtitle, actions, children, className = "", pad = true, as: Tag = "section", ...rest }) {
  return (
    <Tag className={`term-panel min-w-0 ${className}`.trim()} {...rest}>
      {title || actions ? (
        <header className={`flex flex-wrap items-start justify-between gap-2 ${pad ? "px-4 pt-4" : "px-4 pt-3 pb-2"}`}>
          <div className="min-w-0">
            {title ? <h2 className="text-sm font-semibold tracking-tight text-term-text">{title}</h2> : null}
            {subtitle ? <p className="mt-0.5 text-xs text-term-muted">{subtitle}</p> : null}
          </div>
          {actions ? <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
        </header>
      ) : null}
      <div className={pad ? "p-4" + (title || actions ? " pt-3" : "") : ""}>{children}</div>
    </Tag>
  );
}

export { Card, Card as default };
