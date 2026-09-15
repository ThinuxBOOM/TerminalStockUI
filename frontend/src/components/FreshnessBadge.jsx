import React from "react";
import StatusPill from "./StatusPill";

// Thin backwards-compat shim: FreshnessBadge is now a StatusPill with
// provenance-derived freshness. Kept so existing imports/tests keep working.
function FreshnessBadge({ p }) {
  return <StatusPill provenance={p} />;
}

export { FreshnessBadge, FreshnessBadge as default };
