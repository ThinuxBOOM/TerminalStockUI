import React from "react";
import { formatDateTime } from "../utils/format";
function fmtTime(iso) {
  return formatDateTime(iso);
}
function ProvenanceBadge({ p }) {
  if (!p || typeof p !== "object") {
    return /* @__PURE__ */ React.createElement(
      "span",
      {
        className: "inline-flex flex-wrap items-center gap-x-2 gap-y-0.5 rounded border border-term-border bg-term-bg px-2 py-1 text-[10px] text-term-muted",
        title: "provenance=missing"
      },
      /* @__PURE__ */ React.createElement("span", null, "src: ", /* @__PURE__ */ React.createElement("b", { className: "text-term-text" }, "unavailable"))
    );
  }
  const missing = p.missing_fields ?? [];
  return /* @__PURE__ */ React.createElement(
    "span",
    {
      className: "inline-flex flex-wrap items-center gap-x-2 gap-y-0.5 rounded border border-term-border bg-term-bg px-2 py-1 text-[10px] text-term-muted",
      title: `source=${p.source} as_of=${p.as_of} delay=${p.delay_minutes}m grade=${p.quality_grade} fallback=${p.fallback_used} missing=[${missing.join(",")}]`
    },
    /* @__PURE__ */ React.createElement("span", null, "src: ", /* @__PURE__ */ React.createElement("b", { className: "text-term-text" }, p.source)),
    /* @__PURE__ */ React.createElement("span", null, "as_of: ", fmtTime(p.as_of)),
    /* @__PURE__ */ React.createElement("span", null, "delay: ", p.delay_minutes, "m"),
    /* @__PURE__ */ React.createElement("span", null, "Q:", /* @__PURE__ */ React.createElement("b", { className: "text-term-cyan" }, p.quality_grade)),
    p.fallback_used && /* @__PURE__ */ React.createElement("span", { className: "text-term-amber" }, "fallback"),
    missing.length > 0 && /* @__PURE__ */ React.createElement("span", { className: "text-term-red" }, "missing: ", missing.join(","))
  );
}
export { ProvenanceBadge as default };
