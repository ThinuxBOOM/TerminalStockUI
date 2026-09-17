import React from "react";
import { isFxProvenanceMissingError, isFreshFxProvenance } from "../api/client";
import { formatDateTime } from "../utils/format";
function fmtTime(iso) {
  return formatDateTime(iso);
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
      "Cross-market comparison unavailable \u2014 FX provenance missing.",
      !gateError && error ? /* @__PURE__ */ React.createElement("span", { className: "mt-1 block text-[11px] text-term-muted" }, error instanceof Error ? error.message : "FX rank endpoint unreachable.", " ", "Showing native-currency quotes only; no conversion applied.") : null
    );
  }
  const fresh = isFreshFxProvenance(provenance);
  // Fail-closed: stale or fallback FX never renders a fallback badge + stale
  // copy — cross-market comparison is unavailable. Keep provenance title for debug.
  if (!fresh || provenance.fallback_used) {
    return /* @__PURE__ */ React.createElement(
      "div",
      {
        className: "mb-3 rounded border border-term-red bg-term-panel p-3 text-xs text-term-red",
        role: "alert",
        title: `fx_source=${provenance.source} fx_as_of=${provenance.as_of} fx_delay=${provenance.delay_minutes}m fx_grade=${provenance.quality_grade} fx_fallback=${provenance.fallback_used} target=${ccy}`
      },
      /* @__PURE__ */ React.createElement("b", null, "Cross-market comparison unavailable"),
      " \u2014 ",
      "FX unavailable.",
      /* @__PURE__ */ React.createElement("span", { className: "mt-1 block text-[11px] text-term-muted" }, "src ", provenance.source, " \xB7 as of ", fmtTime(provenance.as_of), " \xB7 delay ", provenance.delay_minutes, "m \xB7 grade ", provenance.quality_grade, gateError ? " \xB7 backend gate: FX_PROVENANCE_MISSING \x2014 refresh FX and retry" : " \xB7 showing native-currency quotes only; no conversion applied.")
    );
  }
  const missing = provenance.missing_fields ?? [];
  const border = "border-term-border";
  const tone = "text-term-muted";
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
    missing.length > 0 && /* @__PURE__ */ React.createElement("span", { className: "ml-2 text-term-red" }, "missing: ", missing.join(","))
  );
}
export { FXProvenanceBanner as default };
