import React, { memo, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getMarketLiquidationProxy, liquidationCacheKey } from "../api/liquidation";
import ProvenanceBadge from "./ProvenanceBadge";
import FreshnessBadge from "./FreshnessBadge";
import Skeleton from "./Skeleton";
import EmptyState from "./EmptyState";
import ErrorState from "./ErrorState";

function errorMessage(err, fallback) {
  if (err instanceof Error && err.message) return err.message;
  const e = err;
  const data = e?.response?.data;
  if (typeof data === "string" && data) return data;
  if (data && typeof data === "object") {
    const detail = data.detail ?? data.message;
    if (typeof detail === "string" && detail) return detail;
  }
  if (typeof e?.message === "string" && e.message) return e.message;
  return fallback;
}

function IntensityBar({ value, max }) {
  const w = max > 0 && Number.isFinite(value) ? Math.min(100, Math.max(0, (value / max) * 100)) : 0;
  return (
    <span className="inline-flex min-w-[80px] items-center gap-1" title={`intensity ${Number.isFinite(value) ? value.toFixed(2) : "—"}`}>
      <span className="inline-block h-1.5 w-16 overflow-hidden rounded bg-term-border" role="img" aria-label={`intensity ${w.toFixed(0)}% of max`}>
        <span className="block h-full rounded bg-term-amber" style={{ width: `${w}%` }} />
      </span>
      <span className="font-mono text-[10px] text-term-muted">{Number.isFinite(value) ? value.toFixed(2) : "—"}</span>
    </span>
  );
}

// Per-market liquidation-PROXY table. PROXY labelling is mandatory: every
// number is a volume-anomaly x ATR-range heuristic, NOT exchange liquidation
// data. Empty in -> EmptyState, failures -> ErrorState, never synthetic rows.
function LiquidationCard({ mic, defaultLimit = 20 }) {
  const upper = String(mic ?? "").trim().toUpperCase() || "UNKNOWN";
  const [sort, setSort] = useState("intensity");
  const [limit, setLimit] = useState(defaultLimit);
  const q = useQuery({
    queryKey: liquidationCacheKey(upper, { sort, limit }),
    queryFn: ({ signal }) => getMarketLiquidationProxy(upper, { sort, limit, signal }),
    retry: false,
    staleTime: 120000,
  });
  const data = q.data ?? null;
  const rows = useMemo(() => data?.rows ?? [], [data]);
  const maxI = useMemo(
    () => rows.reduce((m, r) => (Number.isFinite(r.intensity) && r.intensity > m ? r.intensity : m), 0),
    [rows]
  );

  return (
    <div className="min-w-0 rounded border border-term-border bg-term-bg p-3" aria-label={`${upper} liquidation proxy`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="min-w-0 truncate text-sm font-bold text-term-text">{upper} — Liquidation proxy</h4>
        <span className="shrink-0 rounded border border-term-amber px-1.5 py-0.5 text-[10px] font-bold text-term-amber" title="Heuristic from volume-anomaly x ATR-range; not exchange liquidation data">
          PROXY
        </span>
      </div>
      <p className="mt-1 text-[10px] text-term-muted" role="note">
        PROXY — volume-anomaly x ATR-range heuristic, NOT exchange liquidation data. Not investment advice.
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-1" role="group" aria-label={`${upper} liquidation sort`}>
        {[["intensity", "BY INTENSITY"], ["symbol", "BY SYMBOL"]].map(([id, label]) => (
          <button
            key={id}
            type="button"
            onClick={() => setSort(id)}
            aria-pressed={sort === id}
            className={`rounded border px-2 py-0.5 text-[10px] ${sort === id ? "border-term-amber text-term-amber" : "border-term-border text-term-muted"}`}
          >
            {label}
          </button>
        ))}
        <span className="ml-auto flex items-center gap-1 text-[10px] text-term-muted">
          <label htmlFor={`liq-limit-${upper}`}>Top</label>
          <select
            id={`liq-limit-${upper}`}
            value={limit}
            onChange={(e) => setLimit(Math.min(100, Math.max(1, Number(e.target.value) || 20)))}
            className="rounded border border-term-border bg-term-panel px-1 py-0.5 text-[10px] text-term-text"
          >
            {[10, 20, 50].map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
        </span>
      </div>

      <div className="mt-2">
        {q.isLoading && <Skeleton label={`loading ${upper} liquidation proxy…`} lines={5} />}
        {q.isError && (
          <ErrorState
            title={`${upper} liquidation proxy unavailable`}
            detail={errorMessage(q.error, `GET /api/markets/${upper}/liquidation-proxy failed.`)}
            onRetry={() => void q.refetch()}
          />
        )}
        {!q.isLoading && !q.isError && rows.length === 0 && (
          <EmptyState
            title={`No liquidation-proxy rows for ${upper}`}
            detail={data?.provenance?.missing_fields?.includes("liquidation-feed") ? "Exchange liquidation feed unavailable — proxy needs 25+ bars per symbol (see skipped count)." : "Upstream returned no rows."}
            actionLabel="Retry"
            onAction={() => void q.refetch()}
          />
        )}
        {!q.isLoading && !q.isError && rows.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[480px] text-xs">
              <caption className="sr-only">
                {upper} liquidation proxy — heuristic intensity per symbol, not exchange data
              </caption>
              <thead>
                <tr className="border-b border-term-border text-left text-[10px] uppercase tracking-widest text-term-muted">
                  <th scope="col" className="py-1 pr-2">Symbol</th>
                  <th scope="col" className="py-1 pr-2">Side (proxy)</th>
                  <th scope="col" className="py-1 pr-2">Intensity</th>
                  <th scope="col" className="py-1 pr-2 text-right">vol_z</th>
                  <th scope="col" className="py-1 text-right">range/ATR</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.symbol} className="border-b border-term-border">
                    <td className="py-1 pr-2 font-bold text-term-green">{r.symbol}</td>
                    <td className="py-1 pr-2 text-term-muted" title="display heuristic only: close>=open -> short_proxy else long_proxy">
                      {r.side}
                      {r.volume_anomaly && <span className="ml-1 text-term-amber" title="volume anomaly (|vol_z| > 2)">●</span>}
                    </td>
                    <td className="py-1 pr-2"><IntensityBar value={r.intensity} max={maxI} /></td>
                    <td className="py-1 pr-2 text-right font-mono text-[11px] text-term-text">{r.vol_z === null ? "—" : r.vol_z.toFixed(2)}</td>
                    <td className="py-1 text-right font-mono text-[11px] text-term-text">{r.range_atr === null ? "—" : r.range_atr.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {data && (
        <p className="mt-1 text-[10px] text-term-muted" role="status">
          n={data.aggregates.n} · skipped={data.aggregates.skipped} · long_proxy={data.aggregates.long_proxy_n} · short_proxy={data.aggregates.short_proxy_n} · mean {data.aggregates.mean_intensity.toFixed(2)} · max {data.aggregates.max_intensity.toFixed(2)}
        </p>
      )}
      {data?.methodology && (
        <p className="mt-1 text-[10px] text-term-muted" title={data.methodology}>
          Methodology: intensity = max(0, |vol_z(20)| − 2.0) × ((high−low)/ATR14) × (1 + |close−open|/(high−low)).
        </p>
      )}
      {data?.provenance && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <FreshnessBadge p={data.provenance} />
          <ProvenanceBadge p={data.provenance} />
        </div>
      )}
      <p className="mt-1 text-[10px] text-term-muted">
        Source: GET /api/markets/{upper}/liquidation-proxy · missing_fields includes liquidation-feed by design.
      </p>
    </div>
  );
}

const MemoLiquidationCard = memo(LiquidationCard);

function LiquidationSection({ mics }) {
  const list = Array.isArray(mics) && mics.length > 0 ? mics : ["XNYS", "XNAS", "XSHG", "XPAR", "XAMS", "XBRU"];
  const [open, setOpen] = useState(false);
  return (
    <section className="term-panel min-w-0 p-4 md:col-span-3" aria-labelledby="home-liquidation" id="market-liquidation">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="home-liquidation" className="term-label">Liquidation proxy per market · PROXY</h2>
        <button type="button" className="term-btn-ghost text-xs" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
          {open ? "▾ HIDE" : "▸ SHOW"}
        </button>
      </div>
      <p className="mt-1 text-[11px] text-term-muted">
        Deterministic heuristic per market (volume-anomaly x ATR-range) — NOT exchange liquidation data. Collapsed by
        default so 6 markets never fan out on page load.
      </p>
      {open && (
        <div className="mt-3 grid min-w-0 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {list.map((mic) => (
            <MemoLiquidationCard key={mic} mic={mic} />
          ))}
        </div>
      )}
    </section>
  );
}

export { LiquidationCard, LiquidationSection };
export default LiquidationSection;
