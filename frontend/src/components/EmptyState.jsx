import React from "react";
function EmptyState({
  title = "Nothing here yet",
  detail,
  actionLabel,
  onAction,
  className = ""
}) {
  return /* @__PURE__ */ React.createElement("div", { className: `term-panel p-6 text-sm text-term-muted ${className}`, role: "status" }, /* @__PURE__ */ React.createElement("p", { className: "font-bold text-term-text" }, title), detail && /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-xs" }, detail), actionLabel && onAction && /* @__PURE__ */ React.createElement("button", { className: "term-btn-ghost mt-3 text-xs", type: "button", onClick: onAction }, actionLabel));
}
export { EmptyState as default };
