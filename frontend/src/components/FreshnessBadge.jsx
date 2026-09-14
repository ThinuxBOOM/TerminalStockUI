import React from "react";
import { freshnessOf } from "../api/client";
const STYLE = {
  live: "border-term-green text-term-green",
  delayed: "border-term-amber text-term-amber",
  stale: "border-term-red text-term-red",
  cached: "border-term-amber text-term-amber"
};
function FreshnessBadge({ p }) {
  if (!p || typeof p !== "object") {
    return /* @__PURE__ */ React.createElement("span", { className: `rounded border px-2 py-0.5 text-[10px] font-bold tracking-widest ${STYLE.stale}` }, "STALE");
  }
  const f = freshnessOf(p);
  const label = f === "live" ? "LIVE" : f === "delayed" ? `DELAYED ${p.delay_minutes}m` : f === "cached" ? "CACHED / FALLBACK" : "STALE";
  return /* @__PURE__ */ React.createElement("span", { className: `rounded border px-2 py-0.5 text-[10px] font-bold tracking-widest ${STYLE[f]}` }, label);
}
export { FreshnessBadge as default };
