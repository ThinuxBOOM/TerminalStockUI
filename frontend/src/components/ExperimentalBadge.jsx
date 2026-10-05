import React from "react";

// Shown next to every forecast probability. Walk-forward tests found the
// direction probabilities no better than the historical base rate
// (docs/DATA_QUALITY.md, "Measured skill").
function ExperimentalBadge({ status = "experimental" }) {
  if (status && status !== "experimental") return null;
  return (
    <span
      className="rounded border border-term-amber px-1.5 py-0.5 text-[10px] font-bold tracking-wider text-term-amber"
      title="Experimental: in walk-forward tests these up/down probabilities did not beat the historical base rate. The return range and risk figures are the more useful outputs."
    >
      EXPERIMENTAL
    </span>
  );
}

export { ExperimentalBadge, ExperimentalBadge as default };
