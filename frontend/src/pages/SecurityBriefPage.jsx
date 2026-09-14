import React from "react";
import { useParams } from "react-router-dom";
import SecurityBrief from "../features/security/SecurityBrief";
function safeDecode(v) {
  try {
    return decodeURIComponent(v);
  } catch {
    return v;
  }
}
function SecurityBriefPage() {
  const { symbol = "AAPL" } = useParams();
  const decoded = safeDecode(symbol);
  return /* @__PURE__ */ React.createElement("div", null, /* @__PURE__ */ React.createElement("h1", { className: "mb-3 text-sm tracking-widest text-term-muted" }, "SECURITY BRIEF \xB7 ", decoded), /* @__PURE__ */ React.createElement(SecurityBrief, { symbol: decoded }));
}
export { SecurityBriefPage as default };
