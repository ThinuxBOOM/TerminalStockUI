import React from "react";
import { deriveMarketState } from "../api/client";
const STYLE = {
  open: "border-term-green text-term-green",
  closed: "border-term-border text-term-muted",
  lunch: "border-term-amber text-term-amber",
  delayed: "border-term-amber text-term-amber",
  stale: "border-term-red text-term-red"
};
const LABEL = {
  open: "MARKET OPEN",
  closed: "MARKET CLOSED",
  lunch: "LUNCH BREAK",
  delayed: "DELAYED",
  stale: "STALE"
};
function MarketStateBadge({
  state,
  provenance
}) {
  const fallback = provenance ?? {
    source: "unknown",
    as_of: (/* @__PURE__ */ new Date(0)).toISOString(),
    delay_minutes: -1,
    quality_grade: "U",
    fallback_used: true,
    missing_fields: ["market_state"]
  };
  const resolved = deriveMarketState(fallback, state);
  const explicit = typeof state === "string" && state.trim() !== "" ? state : "(derived from provenance)";
  return /* @__PURE__ */ React.createElement(
    "span",
    {
      className: `rounded border px-2 py-0.5 text-[10px] font-bold tracking-widest ${STYLE[resolved]}`,
      title: `market_state=${resolved} explicit=${String(explicit)}`
    },
    LABEL[resolved]
  );
}
export { MarketStateBadge as default };
