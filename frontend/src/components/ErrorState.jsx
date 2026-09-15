import React from "react";
// Fail-closed UI: there is no "partial failure / cached / stale" state.
// Live data renders; anything else is an explicit error with retry.
function ErrorState({
  title = "Something failed",
  detail,
  onRetry
}) {
  return /* @__PURE__ */ React.createElement("div", { className: "term-panel p-6", role: "alert" }, /* @__PURE__ */ React.createElement("p", { className: "text-sm font-bold text-term-red" }, "\u2715 ", title), detail && /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-xs text-term-muted" }, detail), onRetry && /* @__PURE__ */ React.createElement("button", { className: "term-btn-ghost mt-3", type: "button", onClick: onRetry }, "Retry"));
}
export { ErrorState as default };
