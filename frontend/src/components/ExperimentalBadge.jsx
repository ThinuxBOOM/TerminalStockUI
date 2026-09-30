import React from "react";

// Shown next to every forecast probability until the model's track record
// (walk-forward Brier/ECE in the Backtest Lab) says otherwise.
function ExperimentalBadge({ status = "experimental" }) {
  if (status && status !== "experimental") return null;
  return (
    <span
      className="rounded border border-term-amber px-1.5 py-0.5 text-[10px] font-bold tracking-wider text-term-amber"
      title="Experimental: these probabilities have not been validated against realized outcomes. Check the Backtest Lab for the model's measured track record."
    >
      EXPERIMENTAL
    </span>
  );
}

export { ExperimentalBadge, ExperimentalBadge as default };
