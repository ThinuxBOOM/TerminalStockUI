import React from "react";

const VARIANTS = {
  default: "term-panel p-4",
  hero: "term-panel-hero p-4",
  nested: "term-panel-nested p-3",
  elevated: "term-surface-elevated p-4",
};

function Panel({ variant = "default", title, actions, className = "", children, ...rest }) {
  const v = VARIANTS[variant] ?? VARIANTS.default;
  return (
    <section className={`${v} ${className}`.trim()} {...rest}>
      {title || actions ? (
        <div className="mb-2 flex items-center justify-between gap-2">
          {title ? (
            typeof title === "string" ? (
              <h2 className="type-section">{title}</h2>
            ) : (
              title
            )
          ) : <span />}
          {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
        </div>
      ) : null}
      {children}
    </section>
  );
}

export { Panel, Panel as default };
