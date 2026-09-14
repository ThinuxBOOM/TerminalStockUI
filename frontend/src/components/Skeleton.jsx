import React from "react";
function Skeleton({ label = "loading\u2026", lines = 3, className = "" }) {
  const n = Math.min(12, Math.max(1, lines));
  return /* @__PURE__ */ React.createElement("div", { className: `term-panel p-4 ${className}`, role: "status", "aria-label": label }, /* @__PURE__ */ React.createElement("span", { className: "sr-only" }, label), /* @__PURE__ */ React.createElement("div", { className: "space-y-2", "aria-hidden": "true" }, Array.from({ length: n }).map((_, i) => /* @__PURE__ */ React.createElement(
    "div",
    {
      key: i,
      className: "skeleton-shimmer h-3 rounded",
      style: { width: `${92 - i % 3 * 18}%` }
    }
  ))));
}
function SkeletonLine({ className = "" }) {
  return /* @__PURE__ */ React.createElement("div", { className: `skeleton-shimmer h-3 rounded ${className}`, "aria-hidden": "true" });
}
export { SkeletonLine, Skeleton as default };
