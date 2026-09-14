import React from "react";
import { isFxProvenanceMissingError, isFreshFxProvenance } from "../api/client";
function fmtTime(iso) {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}
function FXProvenanceBanner({
  provenance,
  targetCcy,
  loading,
  error
}) {
  const ccy = (targetCcy ?? "").trim().toUpperCase() || "USD";
  if (loading) {
    return /* @__PURE__ */ React.createElement("div", { className: "term-panel p-3 text-xs text-term-muted", role: "status" }, "Loading FX provenance for target ", /* @__PURE__ */ React.createElement("b", { className: "text-term-text" }, ccy), "\u2026");
  }
  const gateError = error !== void 0 && error !== null && isFxProvenanceMissingError(error);
  if (!provenance) {
    return /* @__PURE__ */ React.createElement(
      "div",
      {
        className: "mb-3 rounded border border-term-red bg-term-panel p-3 text-xs text-term-red",
        role: "alert",
        title: `fx_provenance=missing target=${ccy}`
      },
      /* @__PURE__ */ React.createElement("b", null, "FX provenance missing"),
      " \xB7 target ",
      ccy,
      gateError ? " \xB7 backend gate: FX_PROVENANCE_MISSING" : "",
      " \u2014 ",
      "Cross-market comparison unavailable \u2014 FX provenance missing."
    );
  }
  const fresh = isFreshFxProvenance(provenance);
  const missing = provenance.missing_fields ?? [];
  const border = fresh ? "border-term-border" : "border-term-amber";
  const tone = fresh ? "text-term-muted" : "text-term-amber";
  return /* @__PURE__ */ React.createElement(
    "div",
    {
      className: `mb-3 rounded border ${border} bg-term-panel p-3 text-xs ${tone}`,
      role: "status",
      title: `fx_source=${provenance.source} fx_as_of=${provenance.as_of} fx_delay=${provenance.delay_minutes}m fx_grade=${provenance.quality_grade} fx_fallback=${provenance.fallback_used} target=${ccy}`
    },
    /* @__PURE__ */ React.createElement("span", { className: "font-bold tracking-widest" }, "FX PROVENANCE \xB7 TARGET ", ccy),
    /* @__PURE__ */ React.createElement("span", { className: "ml-2" }, "src: ", /* @__PURE__ */ React.createElement("b", { className: "text-term-text" }, provenance.source)),
    /* @__PURE__ */ React.createElement("span", { className: "ml-2" }, "as_of: ", fmtTime(provenance.as_of)),
    /* @__PURE__ */ React.createElement("span", { className: "ml-2" }, "delay: ", provenance.delay_minutes, "m"),
    /* @__PURE__ */ React.createElement("span", { className: "ml-2" }, "Q:", /* @__PURE__ */ React.createElement("b", { className: "text-term-cyan" }, provenance.quality_grade)),
    provenance.fallback_used && /* @__PURE__ */ React.createElement("span", { className: "ml-2 text-term-amber" }, "fallback"),
    missing.length > 0 && /* @__PURE__ */ React.createElement("span", { className: "ml-2 text-term-red" }, "missing: ", missing.join(",")),
    !fresh && /* @__PURE__ */ React.createElement("span", { className: "ml-2 font-bold" }, "\xB7 stale \u2014 Cross-market comparison unavailable \u2014 FX provenance missing.")
  );
}
export { FXProvenanceBanner as default };
