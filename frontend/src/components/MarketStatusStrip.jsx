import React, { memo } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getQuote } from "../api/client";

// Full-width market-status strip: one compact pill per venue in a
// responsive grid (2 / 3 / 6 columns). Probe symbols (JPM/AAPL/...) are
// liveness probes only — a delisted/halted probe reads "unavailable" without
// implying the venue is down; GET /api/markets/overview is the authoritative
// venue state (see MarketLiquidityPanel). Numbers stay on the liquidity
// panel below so this strip scans in one glance.
const VENUES = [
  { mic: "XNYS", short: "NYSE", label: "NYSE (XNYS)", symbol: "JPM" },
  { mic: "XNAS", short: "NASDAQ", label: "NASDAQ (XNAS)", symbol: "AAPL" },
  { mic: "XSHG", short: "SSE", label: "SSE (XSHG)", symbol: "600519.SS" },
  { mic: "XPAR", short: "Paris", label: "Euronext Paris (XPAR)", symbol: "MC.PA" },
  { mic: "XAMS", short: "Amsterdam", label: "Euronext Amsterdam (XAMS)", symbol: "ASML.AS" },
  { mic: "XBRU", short: "Brussels", label: "Euronext Brussels (XBRU)", symbol: "UCB.BR" },
];

const STATE = {
  open: { label: "Open", dot: "bg-term-green" },
  lunch: { label: "Lunch", dot: "bg-term-amber" },
  delayed: { label: "Open", dot: "bg-term-green" },
  closed: { label: "Closed", dot: "bg-term-faint" },
  stale: { label: "Stale", dot: "bg-term-amber" },
};

// Memoized: props are primitives, so parent re-renders skip these pills.
const VenuePill = memo(function VenuePill({ short, mic, symbol, label }) {
  const q = useQuery({
    queryKey: ["quote", symbol],
    queryFn: ({ signal }) => getQuote(symbol, undefined, { signal }),
    retry: false,
    staleTime: 30000,
  });
  const st = STATE[String(q.data?.market_state ?? "").toLowerCase()];
  return (
    <li className="list-none">
      <Link
        to={`/screener?market=${encodeURIComponent(mic === "XNYS" || mic === "XNAS" ? "US" : mic)}`}
        className="inline-flex items-center gap-1.5 rounded-full border border-term-border bg-term-panel px-2.5 py-1 text-2xs transition-colors hover:border-term-border2"
        title={`${label}${q.data?.market_state ? ` · ${q.data.market_state}` : ""}`}
      >
        <span aria-hidden="true" className={`h-1.5 w-1.5 rounded-full ${q.isLoading ? "animate-pulse bg-term-faint" : st ? st.dot : "bg-term-faint"}`} />
        <span className="font-medium text-term-text">{short}</span>
        <span className="text-term-muted">{q.isLoading ? "…" : st ? st.label : "—"}</span>
      </Link>
    </li>
  );
});

function MarketStatusStrip() {
  return (
    <ul className="flex flex-wrap gap-1.5" aria-label="Market status by venue">
      {VENUES.map((v) => (
        <VenuePill key={v.mic} short={v.short} mic={v.mic} symbol={v.symbol} label={v.label} />
      ))}
    </ul>
  );
}

export { MarketStatusStrip as default, VENUES };
