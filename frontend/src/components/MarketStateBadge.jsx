import React from "react";
import StatusPill from "./StatusPill";

// Thin backwards-compat shim: MarketStateBadge is now a StatusPill.
// `mic`/`now` are forwarded so the XSHG lunch override keeps working.
// Kept so existing imports/tests keep working.
function MarketStateBadge({ state, provenance, mic, now }) {
  return <StatusPill marketState={state} provenance={provenance} mic={mic} now={now} />;
}

export { MarketStateBadge, MarketStateBadge as default };
