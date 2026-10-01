import React, { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getTopSignals } from "../../api/signals";
import Card from "../ui/Card";
import Segmented from "../ui/Segmented";
import { RangeBar, RankMeter } from "../charts/Inline";
import { fmtNum, fmtPct, fmtRank } from "../../utils/format";

const HORIZONS = [1, 7, 14, 21].map((h) => ({ value: h, label: `${h}D` }));

function Rows({ rows, scale }) {
  if (!rows.length) return <p className="py-6 text-center text-sm text-term-muted">No scores yet.</p>;
  return (
    <ul className="divide-y divide-term-border/60">
      {rows.map((r) => (
        <li key={r.symbol}>
          <Link to={`/security/${encodeURIComponent(r.symbol)}`} className="grid grid-cols-[minmax(0,1fr)_6.5rem_7rem] items-center gap-3 px-1 py-2 transition-colors hover:bg-term-panel2/70">
            <span className="min-w-0">
              <span className="font-semibold text-term-text">{r.symbol}</span>
              <span className="block truncate text-2xs text-term-muted">{r.company_name}</span>
            </span>
            <span>
              <span className="flex justify-between text-2xs"><span className="text-term-text">{fmtRank(r.out_rank)}</span></span>
              <RankMeter value={r.out_rank} className="mt-1" />
            </span>
            <RangeBar low={r.q10} mid={r.q50} high={r.q90} scale={scale} />
          </Link>
        </li>
      ))}
    </ul>
  );
}

function SignalsTable() {
  const [horizon, setHorizon] = useState(21);
  const q = useQuery({
    queryKey: ["signals-top", horizon],
    queryFn: ({ signal }) => getTopSignals(horizon, 6, { signal }),
    staleTime: 300000,
    retry: false,
  });
  const d = q.data;
  const all = [...(d?.top ?? []), ...(d?.bottom ?? [])];
  const scale = Math.max(0.02, ...all.flatMap((r) => [Math.abs(r.q10 ?? 0), Math.abs(r.q90 ?? 0)]));
  const m = d?.measured ?? {};
  return (
    <Card
      title="Today's strongest signals"
      subtitle={d?.as_of ? `S&P 500 outperformance ranking · scores as of ${d.as_of}` : "S&P 500 outperformance ranking"}
      actions={<Segmented options={HORIZONS} value={horizon} onChange={setHorizon} ariaLabel="Horizon" size="xs" />}
    >
      {q.isLoading ? <div className="h-64 animate-pulse rounded-md bg-term-panel2" /> : (
        <div className="grid gap-x-6 gap-y-4 md:grid-cols-2">
          <div>
            <div className="mb-1 flex items-center justify-between text-2xs font-medium uppercase tracking-[0.08em] text-term-muted"><span>Ranked highest</span><span>rank · {horizon}d range</span></div>
            <Rows rows={d?.top ?? []} scale={scale} />
          </div>
          <div>
            <div className="mb-1 flex items-center justify-between text-2xs font-medium uppercase tracking-[0.08em] text-term-muted"><span>Ranked lowest</span><span>rank · {horizon}d range</span></div>
            <Rows rows={d?.bottom ?? []} scale={scale} />
          </div>
        </div>
      )}
      <p className="mt-3 text-2xs text-term-faint">
        Measured edge at {horizon}d: rank correlation {fmtNum(m.out_ic, 3)} (t = {m.out_ic_t ?? "—"}), top-vs-bottom decile {fmtPct(m.out_decile_spread, 2, { signed: true })} per period. A small edge across many stocks, not a call on any one. <Link to="/screener" className="term-link">Full ranking →</Link>
      </p>
    </Card>
  );
}

export { SignalsTable as default };
