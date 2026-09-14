import React from "react";
import { Link, useParams } from "react-router-dom";
import ForecastDetails from "../features/forecast/ForecastDetails";
function safeDecode(v) {
  try {
    return decodeURIComponent(v);
  } catch {
    return v;
  }
}
function ForecastDetailsPage() {
  const { symbol = "AAPL" } = useParams();
  const decoded = safeDecode(symbol);
  const enc = encodeURIComponent(decoded);
  return /* @__PURE__ */ React.createElement("div", null, /* @__PURE__ */ React.createElement("nav", { className: "mb-3 flex flex-wrap items-center gap-2 text-xs", "aria-label": "Breadcrumb" }, /* @__PURE__ */ React.createElement(Link, { to: "/", className: "text-term-muted hover:text-term-text" }, "\u2190 Home"), /* @__PURE__ */ React.createElement("span", { className: "text-term-muted", "aria-hidden": "true" }, "/"), /* @__PURE__ */ React.createElement(Link, { to: `/security/${enc}`, className: "text-term-muted hover:text-term-text" }, "Security Brief"), /* @__PURE__ */ React.createElement("span", { className: "text-term-muted", "aria-hidden": "true" }, "/"), /* @__PURE__ */ React.createElement(Link, { to: `/backtest?symbol=${enc}`, className: "text-term-muted hover:text-term-text" }, "Backtest")), /* @__PURE__ */ React.createElement("h1", { className: "mb-3 text-sm tracking-widest text-term-muted" }, "FORECAST DETAILS \xB7 ", decoded), /* @__PURE__ */ React.createElement(ForecastDetails, { symbol: decoded }));
}
export { ForecastDetailsPage as default };
