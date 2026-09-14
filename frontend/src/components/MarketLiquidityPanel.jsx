import React, { useState } from "react";
import EmptyState from "./EmptyState";
import ErrorState from "./ErrorState";
import FreshnessBadge from "./FreshnessBadge";
import ProvenanceBadge from "./ProvenanceBadge";
import Skeleton from "./Skeleton";
import { CrossMarketChart, MarketDetailGraphs } from "./MarketGraphs";
import { useMarketDetail } from "../hooks/useMarketLiquidity";
import { isStaleLiquidity } from "../api/markets";
const compactFmt = new Intl.NumberFormat("en", {
  notation: "compact",
  maximumFractionDigits: 1
});
function formatCompact(v) {
  if (v === null || v === void 0 || !Number.isFinite(v)) return "unavailable";
  try {
    return compactFmt.format(v);
  } catch {
    return String(v);
  }
}
function formatSignedPct(v) {
  if (v === null || v === void 0 || !Number.isFinite(v)) {
    return { text: "unavailable", tone: "text-term-muted" };
  }
  const sign = v > 0 ? "+" : "";
  return {
    text: `${sign}${v.toFixed(2)}%`,
    tone: v > 0 ? "text-term-green" : v < 0 ? "text-term-red" : "text-term-muted"
  };
}
function formatPlainPct(v) {
  if (v === null || v === void 0 || !Number.isFinite(v)) return "unavailable";
  return `${v.toFixed(2)}%`;
}
function errorMessage(err) {
  if (err instanceof Error && err.message) return err.message;
  const e = err;
  const data = e?.response?.data;
  if (typeof data === "string" && data) return data;
  if (data && typeof data === "object") {
    const detail = data.detail ?? data.message;
    if (typeof detail === "string" && detail) return detail;
  }
  if (typeof e?.message === "string" && e.message) return e.message;
  return "Backend /api/markets/overview unreachable and screener fallback failed.";
}
function MarketCard({ m }) {
  const total = m.total > 0 ? m.total : m.advancers + m.decliners + m.unchanged;
  const advW = total > 0 ? m.advancers / total * 100 : 0;
  const decW = total > 0 ? m.decliners / total * 100 : 0;
  const unchW = total > 0 ? Math.max(0, 100 - advW - decW) : 0;
  const stale = isStaleLiquidity(m.provenance);
  const avg = formatSignedPct(m.avg_change_pct);
  const stateEntries = Object.entries(m.market_state_counts ?? {});
  return /* @__PURE__ */ React.createElement(
    "article",
    {
      className: "min-w-0 rounded border border-term-border bg-term-bg p-3",
      "aria-label": `${m.label || m.mic} liquidity and breadth`
    },
    /* @__PURE__ */ React.createElement("div", { className: "flex items-center justify-between gap-2" }, /* @__PURE__ */ React.createElement("h3", { className: "min-w-0 truncate text-sm font-bold text-term-text" }, m.label || m.mic), /* @__PURE__ */ React.createElement("span", { className: "shrink-0 text-[10px] text-term-muted" }, "n=", total)),
    /* @__PURE__ */ React.createElement(
      "div",
      {
        className: "mt-2 flex h-2 w-full overflow-hidden rounded bg-term-border",
        role: "img",
        "aria-label": `${m.mic}: ${m.advancers} advancers, ${m.decliners} decliners, ${m.unchanged} unchanged`,
        title: `adv ${m.advancers} / dec ${m.decliners} / unch ${m.unchanged}`
      },
      /* @__PURE__ */ React.createElement("div", { className: "h-full bg-term-green", style: { width: `${advW}%` } }),
      /* @__PURE__ */ React.createElement("div", { className: "h-full bg-term-red", style: { width: `${decW}%` } }),
      /* @__PURE__ */ React.createElement("div", { className: "h-full bg-term-muted", style: { width: `${unchW}%` } })
    ),
    /* @__PURE__ */ React.createElement("div", { className: "mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px]" }, /* @__PURE__ */ React.createElement("span", { className: "text-term-green" }, "\u25B2 ", m.advancers), /* @__PURE__ */ React.createElement("span", { className: "text-term-red" }, "\u25BC ", m.decliners), /* @__PURE__ */ React.createElement("span", { className: "text-term-muted" }, "\u25A0 ", m.unchanged)),
    /* @__PURE__ */ React.createElement("dl", { className: "mt-2 space-y-1 text-xs" }, /* @__PURE__ */ React.createElement("div", { className: "flex items-center justify-between gap-2" }, /* @__PURE__ */ React.createElement("dt", { className: "text-term-muted" }, "Avg change"), /* @__PURE__ */ React.createElement("dd", { className: `font-bold ${avg.tone}` }, avg.text)), /* @__PURE__ */ React.createElement("div", { className: "flex items-center justify-between gap-2" }, /* @__PURE__ */ React.createElement("dt", { className: "text-term-muted" }, "Volume"), /* @__PURE__ */ React.createElement("dd", { className: "text-term-text" }, formatCompact(m.total_volume))), /* @__PURE__ */ React.createElement("div", { className: "flex items-center justify-between gap-2" }, /* @__PURE__ */ React.createElement("dt", { className: "text-term-muted" }, "Turnover"), /* @__PURE__ */ React.createElement("dd", { className: "text-term-text", title: m.turnover_note ?? void 0 }, formatCompact(m.turnover))), m.turnover_note && /* @__PURE__ */ React.createElement("p", { className: "text-[10px] text-term-muted", role: "note" }, "Turnover sums native price\xD7volume with no FX conversion \u2014 cross-currency totals aren't comparable."), /* @__PURE__ */ React.createElement("div", { className: "flex items-center justify-between gap-2" }, /* @__PURE__ */ React.createElement("dt", { className: "text-term-muted" }, "Avg range"), /* @__PURE__ */ React.createElement("dd", { className: "text-term-text" }, formatPlainPct(m.avg_range_pct)))),
    /* @__PURE__ */ React.createElement("div", { className: "mt-2 flex flex-wrap gap-1", "aria-label": `${m.mic} market states` }, stateEntries.length === 0 && /* @__PURE__ */ React.createElement("span", { className: "text-[10px] text-term-muted" }, "no state data"), stateEntries.map(([state, count]) => /* @__PURE__ */ React.createElement(
      "span",
      {
        key: state,
        className: "rounded border border-term-border px-1.5 py-0.5 text-[10px] text-term-muted",
        title: `${count} instrument(s) with market_state=${state}`
      },
      state,
      "\xD7",
      count
    ))),
    /* @__PURE__ */ React.createElement("div", { className: "mt-2 flex flex-wrap items-center gap-1.5" }, /* @__PURE__ */ React.createElement(FreshnessBadge, { p: m.provenance }), /* @__PURE__ */ React.createElement(ProvenanceBadge, { p: m.provenance })),
    stale && /* @__PURE__ */ React.createElement("p", { className: "mt-1.5 text-[10px] text-term-amber", role: "note" }, "Stale/fallback figures \u2014 shown for context, never ranked.")
  );
}
function MarketCardWithGraphs({ m }) {
  const [expanded, setExpanded] = useState(false);
  const detail = useMarketDetail(m.mic, expanded);
  const rows = detail.data?.rows?.length ? detail.data.rows : m.rows ?? [];
  return /* @__PURE__ */ React.createElement("div", { className: "min-w-0" }, /* @__PURE__ */ React.createElement(MarketCard, { m }), /* @__PURE__ */ React.createElement(
    "button",
    {
      type: "button",
      className: "term-btn-ghost mt-1 text-xs",
      onClick: () => setExpanded((v) => !v),
      "aria-expanded": expanded,
      "aria-label": `${expanded ? "Hide" : "Show"} ${m.mic} symbol graphs`
    },
    expanded ? "\u25BE HIDE GRAPHS" : "\u25B8 SHOW GRAPHS"
  ), expanded && /* @__PURE__ */ React.createElement("div", { className: "mt-2" }, detail.isLoading && /* @__PURE__ */ React.createElement(Skeleton, { label: `loading ${m.mic} symbols\u2026`, lines: 3 }), detail.isError && /* @__PURE__ */ React.createElement(
    ErrorState,
    {
      title: `${m.mic} detail unavailable`,
      detail: detail.error instanceof Error ? detail.error.message : "Per-symbol endpoint unreachable.",
      onRetry: () => void detail.refetch()
    }
  ), !detail.isLoading && !detail.isError && /* @__PURE__ */ React.createElement(MarketDetailGraphs, { rows })));
}
function MarketLiquidityPanel({
  data,
  isLoading,
  isError,
  error,
  onRetry
}) {
  if (isLoading) {
    return /* @__PURE__ */ React.createElement(
      "section",
      {
        className: "term-panel min-w-0 p-4 md:col-span-3 md:row-start-2",
        "aria-labelledby": "home-liquidity"
      },
      /* @__PURE__ */ React.createElement("h2", { id: "home-liquidity", className: "term-label" }, "Market liquidity & breadth"),
      /* @__PURE__ */ React.createElement("div", { className: "mt-2" }, /* @__PURE__ */ React.createElement(Skeleton, { label: "loading market liquidity\u2026", lines: 6 }))
    );
  }
  if (isError) {
    return /* @__PURE__ */ React.createElement(
      "section",
      {
        className: "term-panel min-w-0 p-4 md:col-span-3 md:row-start-2",
        "aria-labelledby": "home-liquidity"
      },
      /* @__PURE__ */ React.createElement("h2", { id: "home-liquidity", className: "term-label" }, "Market liquidity & breadth"),
      /* @__PURE__ */ React.createElement("div", { className: "mt-2" }, /* @__PURE__ */ React.createElement(
        ErrorState,
        {
          title: "Market liquidity unavailable",
          detail: errorMessage(error),
          onRetry
        }
      ))
    );
  }
  const markets = data?.markets ?? [];
  if (markets.length === 0) {
    return /* @__PURE__ */ React.createElement(
      "section",
      {
        className: "term-panel min-w-0 p-4 md:col-span-3 md:row-start-2",
        "aria-labelledby": "home-liquidity"
      },
      /* @__PURE__ */ React.createElement("h2", { id: "home-liquidity", className: "term-label" }, "Market liquidity & breadth"),
      /* @__PURE__ */ React.createElement("div", { className: "mt-2" }, /* @__PURE__ */ React.createElement(
        EmptyState,
        {
          title: "No market breadth yet",
          detail: "The screener returned no rows for XNYS / XNAS / XSHG / XPAR / XAMS / XBRU.",
          actionLabel: onRetry ? "Retry" : void 0,
          onAction: onRetry
        }
      ))
    );
  }
  return /* @__PURE__ */ React.createElement(
    "section",
    {
      className: "term-panel min-w-0 p-4 md:col-span-3 md:row-start-2",
      "aria-labelledby": "home-liquidity"
    },
    /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center justify-between gap-2" }, /* @__PURE__ */ React.createElement("h2", { id: "home-liquidity", className: "term-label" }, "Market liquidity & breadth"), data && /* @__PURE__ */ React.createElement(FreshnessBadge, { p: data.provenance })),
    data?.fallback_used && /* @__PURE__ */ React.createElement(
      "p",
      {
        className: "mt-2 rounded border border-term-amber p-2 text-[11px] text-term-amber",
        role: "note"
      },
      "Client-side fallback \u2014 breadth computed from screener snapshots (backend /api/markets/overview not deployed). Figures may be delayed/partial; markets are NOT ranked."
    ),
    /* @__PURE__ */ React.createElement("div", { className: "mt-3" }, /* @__PURE__ */ React.createElement(CrossMarketChart, { markets })),
    /* @__PURE__ */ React.createElement("div", { className: "mt-3 grid min-w-0 gap-3 sm:grid-cols-2 xl:grid-cols-3" }, markets.map((m) => /* @__PURE__ */ React.createElement(MarketCardWithGraphs, { key: m.mic, m }))),
    /* @__PURE__ */ React.createElement("div", { className: "mt-3 flex flex-wrap items-center gap-1.5" }, data && /* @__PURE__ */ React.createElement(ProvenanceBadge, { p: data.provenance })),
    /* @__PURE__ */ React.createElement("p", { className: "mt-2 text-[10px] text-term-muted" }, "Breadth = advancers/decliners from latest screener change_pct per MIC. Not investment advice.")
  );
}
export { MarketLiquidityPanel as default };
