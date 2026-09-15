import React from "react";

// Shimmer skeleton. `variant` tunes bar widths to mimic real content:
//   "table" — symbol (short, bold-ish) + price (right-aligned numeric) rows;
//   "chart"/"price" — one wide hero bar + tapering detail bars;
//   default/unknown — generic tapering bars (no variant prop needed).
// Widths are proportions only (percentages), never fake data.
function Skeleton({ label = "loading…", lines = 3, className = "", variant }) {
  const n = Math.min(12, Math.max(1, lines));
  if (variant === "table") {
    return /* @__PURE__ */ React.createElement("div", { className: `term-panel p-4 ${className}`, role: "status", "aria-label": label },
      /* @__PURE__ */ React.createElement("span", { className: "sr-only" }, label),
      /* @__PURE__ */ React.createElement("div", { className: "space-y-2", "aria-hidden": "true" }, Array.from({ length: n }).map((_, i) => /* @__PURE__ */ React.createElement(
        "div",
        { key: i, className: "flex items-center gap-3" },
        /* @__PURE__ */ React.createElement("div", { className: "skeleton-shimmer h-3 rounded", style: { width: i % 4 === 3 ? "18%" : "12%" } }),
        /* @__PURE__ */ React.createElement("div", { className: "skeleton-shimmer h-3 flex-1 rounded" }),
        /* @__PURE__ */ React.createElement("div", { className: "skeleton-shimmer h-3 rounded", style: { width: "16%" } })
      ))));
  }
  if (variant === "chart" || variant === "price") {
    return /* @__PURE__ */ React.createElement("div", { className: `term-panel p-4 ${className}`, role: "status", "aria-label": label },
      /* @__PURE__ */ React.createElement("span", { className: "sr-only" }, label),
      /* @__PURE__ */ React.createElement("div", { className: "space-y-2", "aria-hidden": "true" },
        /* @__PURE__ */ React.createElement("div", { className: "skeleton-shimmer h-24 rounded" }),
        Array.from({ length: Math.max(1, n - 1) }).map((_, i) => /* @__PURE__ */ React.createElement(
          "div",
          {
            key: i,
            className: "skeleton-shimmer h-3 rounded",
            style: { width: `${88 - i % 3 * 22}%` }
          }
        ))));
  }
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
