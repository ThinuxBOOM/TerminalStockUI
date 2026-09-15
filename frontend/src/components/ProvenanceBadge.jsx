import React from "react";
import StatusPill from "./StatusPill";

// Thin backwards-compat shim: ProvenanceBadge is now a StatusPill rendered
// large so the quality-grade superscript stays visible; full provenance detail
// lives in the tooltip + info popover. Kept so existing imports/tests keep working.
function ProvenanceBadge({ p }) {
  return <StatusPill provenance={p} size="lg" />;
}

export { ProvenanceBadge, ProvenanceBadge as default };
