import React, { memo } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getQuote } from "../api/client";
import StatusPill from "./StatusPill";
import { SkeletonLine } from "./Skeleton";

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

// Memoized: props are primitives, so parent re-renders skip these pills.
const VenuePill = memo(function VenuePill({ short, mic, symbol }) {
  const q = useQuery({
    queryKey: ["quote", symbol],
    queryFn: ({ signal }) => getQuote(symbol, undefined, { signal }),
    retry: false,
    staleTime: 30000,
  });
  return (
    <li className="min-w-0 list-none">
      <Link
        to={`/screener?market=${encodeURIComponent(mic)}`}
        className="block min-w-0 rounded border border-term-border bg-term-bg px-3 py-2 transition-colors hover:border-term-border2 hover:bg-term-panel2 focus-visible:border-term-green"
        aria-label={`${short} (${mic}) market status — view market board`}
        title={`${short} (${mic}) · probe ${symbol} — click for market board`}
      >
        <div className="flex items-baseline justify-between gap-2">
          <span className="truncate text-sm font-bold text-term-text">{short}</span>
          <span className="tnum shrink-0 text-[10px] text-term-muted">{mic}</span>
        </div>
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5" role="status" aria-live="polite" aria-label={`${short} state loading status`}>
          {q.isLoading && (
            <span className="w-16" role="status" aria-label="loading">
              <SkeletonLine />
            </span>
          )}
          {q.isError && (
            <span
              className="text-xs text-term-muted underline decoration-dotted"
              title={q.error instanceof Error ? q.error.message : "Probe quote failed — venue may still be live (see overview)."}
              role="button"
              tabIndex={0}
              onClick={(e) => { e.preventDefault(); void q.refetch(); }}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); void q.refetch(); } }}
              aria-label={`Retry ${symbol} probe`}
            >
              unavailable — retry
            </span>
          )}
          {q.data && (
            <StatusPill
              marketState={q.data.market_state}
              provenance={q.data.provenance}
              mic={mic}
            />
          )}
        </div>
      </Link>
    </li>
  );
});

function MarketStatusStrip() {
  return (
    <section className="term-panel-hero min-w-0 p-4" aria-labelledby="home-market-status">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="home-market-status" className="term-label">
          Market status
        </h2>
        <a href="#market-board" className="text-[11px] text-term-green hover:underline focus-visible:underline">
          Market board ↓
        </a>
      </div>
      <ul className="mt-2 grid min-w-0 grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6">
        {VENUES.map((v) => (
          <VenuePill key={v.mic} short={v.short} mic={v.mic} symbol={v.symbol} />
        ))}
      </ul>
      <p className="mt-2 text-[10px] text-term-muted">
        Live per-venue state from quote market_state + health — never hardcoded. Click a venue for its board.
      </p>
    </section>
  );
}

export { MarketStatusStrip as default, VENUES };
