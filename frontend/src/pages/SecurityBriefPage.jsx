import React from "react";
import { Link, useParams } from "react-router-dom";
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
  const enc = encodeURIComponent(decoded);
  return /* @__PURE__ */ React.createElement("div", null, /* @__PURE__ */ React.createElement("nav", { className: "mb-3 flex flex-wrap items-center gap-2 text-xs", "aria-label": "Breadcrumb" }, /* @__PURE__ */ React.createElement(Link, { to: "/", className: "text-term-muted hover:text-term-text" }, "\u2190 Home"), /* @__PURE__ */ React.createElement("span", { className: "text-term-muted", "aria-hidden": "true" }, "/"), /* @__PURE__ */ React.createElement(Link, { to: `/forecast/${enc}`, className: "text-term-muted hover:text-term-text" }, "Forecast"), /* @__PURE__ */ React.createElement("span", { className: "text-term-muted", "aria-hidden": "true" }, "/"), /* @__PURE__ */ React.createElement(Link, { to: `/backtest?symbol=${enc}`, className: "text-term-muted hover:text-term-text" }, "Backtest")), /* @__PURE__ */ React.createElement("h1", { className: "mb-3 text-sm tracking-widest text-term-muted" }, "SECURITY BRIEF \xB7 ", decoded), /* @__PURE__ */ React.createElement(SecurityBrief, { symbol: decoded }));
}
export { SecurityBriefPage as default };
