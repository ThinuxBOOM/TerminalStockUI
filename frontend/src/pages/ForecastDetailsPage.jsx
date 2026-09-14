import React from "react";
import { useParams } from "react-router-dom";
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
  return /* @__PURE__ */ React.createElement("div", null, /* @__PURE__ */ React.createElement("h1", { className: "mb-3 text-sm tracking-widest text-term-muted" }, "FORECAST DETAILS \xB7 ", decoded), /* @__PURE__ */ React.createElement(ForecastDetails, { symbol: decoded }));
}
export { ForecastDetailsPage as default };
