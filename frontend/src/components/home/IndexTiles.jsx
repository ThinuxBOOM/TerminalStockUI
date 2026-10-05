import React, { memo } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getChart } from "../../api/client";
import { Sparkline } from "../charts/Inline";
import { fmtMoney, fmtPct } from "../../utils/format";

const isIndex = (s) => s.startsWith("^") || s === "000001.SS";

const BENCHMARKS = [
  { symbol: "SPY", label: "S&P 500", venue: "US" },
  { symbol: "QQQ", label: "Nasdaq 100", venue: "US" },
  { symbol: "000001.SS", label: "SSE Composite", venue: "Shanghai" },
  { symbol: "^FCHI", label: "CAC 40", venue: "Paris" },
  { symbol: "^AEX", label: "AEX", venue: "Amsterdam" },
  { symbol: "^BFX", label: "BEL 20", venue: "Brussels" },
];

const Tile = memo(function Tile({ symbol, label, venue }) {
  const q = useQuery({
    queryKey: ["chart", symbol, "1d", 30],
    queryFn: ({ signal }) => getChart(symbol, "1d", 30, { signal }),
    retry: false,
    staleTime: 120000,
  });
  const quote = q.data?.quote;
  const closes = (q.data?.candles ?? []).map((c) => c.close);
  const chg = typeof quote?.change_pct === "number" ? quote.change_pct / 100 : null;
  const first = closes[0];
  const month = first && quote?.price ? quote.price / first - 1 : null;
  return (
    <Link
      to={`/security/${encodeURIComponent(symbol)}`}
      className="term-panel group flex min-w-0 flex-col gap-1 p-3 transition-colors hover:border-term-border2"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-xs font-medium text-term-text">{label}</span>
        <span className="text-2xs text-term-faint">{venue}</span>
      </div>
      {q.isLoading ? (
        <div className="h-12 animate-pulse rounded bg-term-panel2" />
      ) : quote ? (
        <div className="flex items-end justify-between gap-2">
          <div className="min-w-0">
            <div className="term-num truncate text-base font-semibold text-term-text">{isIndex(symbol) ? quote.price?.toLocaleString(undefined, { maximumFractionDigits: 0 }) : fmtMoney(quote.price, quote.currency || "USD")}</div>
            <div className={`term-num text-xs ${chg > 0 ? "text-term-green" : chg < 0 ? "text-term-red" : "text-term-muted"}`}>
              {fmtPct(chg, 2, { signed: true })}
              <span className="ml-1.5 text-term-faint">1M {fmtPct(month, 1, { signed: true })}</span>
            </div>
          </div>
          <Sparkline values={closes} width={72} height={30} color={month !== null && month < 0 ? "var(--div-neg)" : "var(--series-1)"} />
        </div>
      ) : (
        <div className="text-xs text-term-faint">Unavailable</div>
      )}
    </Link>
  );
});

function IndexTiles() {
  return (
    <section aria-label="Benchmarks" className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
      {BENCHMARKS.map((b) => <Tile key={b.symbol} {...b} />)}
    </section>
  );
}

export { BENCHMARKS, IndexTiles as default };
