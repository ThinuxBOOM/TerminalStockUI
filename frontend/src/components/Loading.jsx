import React from "react";
function Loading({ label = "loading\u2026" }) {
  return /* @__PURE__ */ React.createElement("div", { className: "term-panel p-6 text-sm text-term-muted", role: "status" }, /* @__PURE__ */ React.createElement("span", { className: "animate-pulse text-term-green" }, "\u258A"), " ", label);
}
export { Loading as default };
