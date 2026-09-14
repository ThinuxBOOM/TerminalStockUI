import React from "react";
function StaleBanner({ detail }) {
  return /* @__PURE__ */ React.createElement("div", { className: "mb-3 rounded border border-term-amber bg-term-panel p-3 text-xs text-term-amber", role: "alert" }, "\u26A0 Partial failure \u2014 showing cached/stale data", detail ? `: ${detail}` : ".", " Provider outage does not block the page (Milestone 1 acceptance).");
}
function ErrorState({
  title = "Something failed",
  detail,
  onRetry
}) {
  return /* @__PURE__ */ React.createElement("div", { className: "term-panel p-6", role: "alert" }, /* @__PURE__ */ React.createElement("p", { className: "text-sm font-bold text-term-red" }, "\u2715 ", title), detail && /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-xs text-term-muted" }, detail), onRetry && /* @__PURE__ */ React.createElement("button", { className: "term-btn-ghost mt-3", type: "button", onClick: onRetry }, "Retry"));
}
export { StaleBanner, ErrorState as default };
