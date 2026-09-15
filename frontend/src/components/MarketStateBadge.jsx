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
  provenance,
  mic,
  now
}) {
  const fallback = provenance ?? {
    source: "unknown",
    as_of: (/* @__PURE__ */ new Date(0)).toISOString(),
    delay_minutes: -1,
    quality_grade: "U",
    fallback_used: true,
    missing_fields: ["market_state"]
  };
  let resolved = deriveMarketState(fallback, state);
  // XSHG lunch override: 11:30-13:00 Asia/Shanghai maps open -> lunch.
  // `mic` + `now` are optional (backwards compatible); `now` defaults to now.
  try {
    const upper = String(mic ?? "").trim().toUpperCase();
    if (upper === "XSHG" && resolved === "open") {
      const at = now instanceof Date ? now : now ? new Date(now) : new Date();
      // Inline lunch check to avoid a hard import cycle; mirrors
      // isXshgLunchWindow() in src/api/markets.js.
      const fmt = new Intl.DateTimeFormat("en-US", {
        timeZone: "Asia/Shanghai",
        weekday: "short",
        hour: "numeric",
        minute: "numeric",
        second: "numeric",
        hourCycle: "h23"
      });
      const parts = fmt.formatToParts(at);
      const get = (t) => parts.find((p) => p.type === t)?.value;
      const wd = String(get("weekday") ?? "");
      if (wd !== "Sat" && wd !== "Sun") {
        let h = Number(get("hour"));
        if (h === 24) h = 0;
        const mi = Number(get("minute"));
        const se = Number(get("second"));
        if (Number.isFinite(h) && Number.isFinite(mi) && Number.isFinite(se)) {
          const mins = h * 60 + mi + se / 60;
          if (mins >= 11 * 60 + 30 && mins < 13 * 60) resolved = "lunch";
        }
      }
    }
  } catch {
    // never break badge rendering on Intl failures
  }
  const explicit = typeof state === "string" && state.trim() !== "" ? state : "(derived from provenance)";
  return /* @__PURE__ */ React.createElement(
    "span",
    {
      className: `rounded border px-2 py-0.5 text-[10px] font-bold tracking-widest ${STYLE[resolved]}`,
      title: `market_state=${resolved} explicit=${String(explicit)}${mic ? ` mic=${String(mic).toUpperCase()}` : ""}`
    },
    LABEL[resolved]
  );
}
export { MarketStateBadge as default };
