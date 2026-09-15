import React, { memo } from "react";
import { useQuery } from "@tanstack/react-query";
import { getQuote } from "../api/client";
import FreshnessBadge from "./FreshnessBadge";
import MarketStateBadge from "./MarketStateBadge";
import { SkeletonLine } from "./Skeleton";

// Full-width market-status strip: one compact pill per venue in a
// responsive grid (2 / 3 / 6 columns). Replaces the old narrow 1-column
// card that left a two-column hole beside it on desktop. Short venue names
// (never truncated) + live state badges; numbers stay on the liquidity
// panel below so this strip scans in one glance.
const VENUES = [
  { mic: "XNYS", short: "NYSE", label: "NYSE (XNYS)", symbol: "JPM" },
  { mic: "XNAS", short: "NASDAQ", label: "NASDAQ (XNAS)", symbol: "AAPL" },
  { mic: "XSHG", short: "SSE", label: "SSE (XSHG)", symbol: "600519.SS" },
  { mic: "XPAR", short: "Paris", label: "Euronext Paris (XPAR)", symbol: "MC.PA" },
  { mic: "XAMS", short: "Amsterdam", label: "Euronext Amsterdam (XAMS)", symbol: "ASML.AS" },
  { mic: "XBRU", short: "Brussels", label: "Euronext Brussels (XBRU)", symbol: "UCB.BR" },
];

// Memoized: props are primitives, so parent re-renders skip these pills.
const VenuePill = memo(function VenuePill({ short, mic, symbol }) {
  const q = useQuery({
    queryKey: ["quote", symbol],
    queryFn: ({ signal }) => getQuote(symbol, undefined, { signal }),
    retry: false,
    staleTime: 30000,
  });
  return (
    <div
      className="min-w-0 rounded border border-term-border bg-term-bg px-3 py-2"
      aria-label={`${short} (${mic}) market status`}
      title={`${short} (${mic}) · probe ${symbol}`}
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="truncate text-sm font-bold text-term-text">{short}</span>
        <span className="shrink-0 text-[10px] text-term-muted">{mic}</span>
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-1.5" role="status" aria-label={`${short} state loading status`}>
        {q.isLoading && (
          <span className="w-16" role="status" aria-label="loading">
            <SkeletonLine />
          </span>
        )}
        {q.isError && <span className="text-xs text-term-muted">unavailable</span>}
        {q.data && (
          <>
            <MarketStateBadge state={q.data.market_state} provenance={q.data.provenance} />
            <FreshnessBadge p={q.data.provenance} />
          </>
        )}
      </div>
    </div>
  );
});

function MarketStatusStrip() {
  return (
    <section className="term-panel min-w-0 p-4" aria-labelledby="home-market-status">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="home-market-status" className="term-label">
          Market status
        </h2>
        <a href="#market-indices" className="text-[11px] text-term-green hover:underline">
          Market indices &amp; Top-20 composites ↓
        </a>
      </div>
      <div className="mt-2 grid min-w-0 grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6">
        {VENUES.map((v) => (
          <VenuePill key={v.mic} short={v.short} mic={v.mic} symbol={v.symbol} />
        ))}
      </div>
      <p className="mt-2 text-[10px] text-term-muted">
        Live per-venue state from quote market_state + health — never hardcoded.
      </p>
    </section>
  );
}

export { MarketStatusStrip as default, VENUES };
