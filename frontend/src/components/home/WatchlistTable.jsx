import React, { memo, useMemo } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { X } from "lucide-react";
import { getQuote } from "../../api/client";
import { getScreener } from "../../api/screener";
import StatusPill from "../StatusPill";
import { RangeBar, RankMeter } from "../charts/Inline";
import { fmtMoney, fmtPct, fmtRank } from "../../utils/format";

// Live price per row (quotes cache 30s) + the day's model scores in one call.
const PriceCells = memo(function PriceCells({ symbol }) {
  const q = useQuery({
    queryKey: ["quote", symbol],
    queryFn: ({ signal }) => getQuote(symbol, undefined, { signal }),
    retry: false,
    staleTime: 30000,
  });
  const d = q.data;
  const chg = typeof d?.change_pct === "number" ? d.change_pct / 100 : null;
  return (
    <>
      <td className="term-num text-right text-term-text">{q.isLoading ? "…" : fmtMoney(d?.price, d?.currency || "USD")}</td>
      <td className={`term-num text-right ${chg > 0 ? "text-term-green" : chg < 0 ? "text-term-red" : "text-term-muted"}`}>{q.isLoading ? "" : fmtPct(chg, 2, { signed: true })}</td>
      <td className="hidden 2xl:table-cell">{d ? <StatusPill freshness={d.provenance} marketState={d.market_state} provenance={d.provenance} size="sm" /> : null}</td>
    </>
  );
});

function WatchlistTable({ symbols, onRemove, horizon = 21, compact = false }) {
  const navigate = useNavigate();
  const scoresQ = useQuery({
    queryKey: ["watch-scores", horizon, symbols.join(",")],
    queryFn: ({ signal }) => getScreener({ market: "ALL", horizon, symbols: symbols.join(","), limit: 200, sort: "symbol", order: "asc" }, { signal }),
    enabled: symbols.length > 0,
    staleTime: 300000,
    retry: false,
  });
  const scores = useMemo(() => Object.fromEntries((scoresQ.data?.rows ?? []).map((r) => [r.symbol.toUpperCase(), r])), [scoresQ.data]);
  const scale = Math.max(0.02, ...Object.values(scores).flatMap((r) => [Math.abs(r.q10 ?? 0), Math.abs(r.q90 ?? 0)]));
  if (!symbols.length) {
    return <p className="py-6 text-center text-sm text-term-muted">Your watchlist is empty. Use ☆ Watch on any stock to add it.</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="term-table min-w-[560px]">
        <thead>
          <tr>
            <th>Stock</th>
            <th className="text-right">Price</th>
            <th className="text-right">Day</th>
            <th className="hidden 2xl:table-cell">Status</th>
            {!compact ? <th>Rank ({horizon}d)</th> : null}
            <th>{horizon}d range</th>
            {!compact ? <th className="text-right">Drop risk</th> : null}
            {onRemove ? <th className="w-8"><span className="sr-only">Remove</span></th> : null}
          </tr>
        </thead>
        <tbody>
          {symbols.map((s) => {
            const r = scores[s.toUpperCase()];
            return (
              <tr key={s} className="cursor-pointer" tabIndex={0} onClick={() => navigate(`/security/${encodeURIComponent(s)}`)} onKeyDown={(e) => { if (e.key === "Enter") navigate(`/security/${encodeURIComponent(s)}`); }}>
                <td>
                  <Link to={`/security/${encodeURIComponent(s)}`} className="font-semibold text-term-text" onClick={(e) => e.stopPropagation()}>{s}</Link>
                  {r?.company_name && r.company_name !== s ? <div className="max-w-[12rem] truncate text-2xs text-term-muted">{r.company_name}</div> : null}
                </td>
                <PriceCells symbol={s} />
                {!compact ? (
                  <td className="w-36">
                    {r?.out_rank != null ? (<><div className="text-2xs text-term-text">{fmtRank(r.out_rank)}</div><RankMeter value={r.out_rank} className="mt-1" /></>) : <span className="text-2xs text-term-faint">{scoresQ.isLoading ? "…" : "—"}</span>}
                  </td>
                ) : null}
                <td className="w-48">{r ? <RangeBar low={r.q10} mid={r.q50} high={r.q90} scale={scale} showLabels={!compact} /> : <span className="text-2xs text-term-faint">{scoresQ.isLoading ? "…" : "not scored yet"}</span>}</td>
                {!compact ? <td className="term-num text-right text-term-text">{fmtPct(r?.drawdown_prob, 0)}</td> : null}
                {onRemove ? (
                  <td>
                    <button type="button" className="term-icon-btn" aria-label={`Remove ${s}`} onClick={(e) => { e.stopPropagation(); onRemove(s); }}>
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </td>
                ) : null}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export { WatchlistTable as default };
