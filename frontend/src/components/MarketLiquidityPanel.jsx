import React, { memo, useState } from "react";
import EmptyState from "./EmptyState";
import ErrorState from "./ErrorState";
import FreshnessBadge from "./FreshnessBadge";
import MarketStateBadge from "./MarketStateBadge";
import ProvenanceBadge from "./ProvenanceBadge";
import Skeleton from "./Skeleton";
import {
  BreadthBar,
  CrossMarketChart,
  LiquiditySparkline,
  MarketDetailGraphs,
  NativeMeter,
  RangeBar,
} from "./MarketGraphs";
import { useMarketDetail, useMarketLiquidityHistory } from "../hooks/useMarketLiquidity";
import {
  MARKET_CURRENCIES,
  deriveMarketCardState,
  isStaleLiquidity,
} from "../api/markets";

const compactFmt = new Intl.NumberFormat("en", {
  notation: "compact",
  maximumFractionDigits: 1,
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
    tone: v > 0 ? "text-term-green" : v < 0 ? "text-term-red" : "text-term-muted",
  };
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

// Memoized: `m` is a stable normalized object per market, so expanding
// one card's graphs doesn't re-render every other card.
// Fail-closed: stale/fallback provenance never renders a table —
// isStaleLiquidity is an error gate to ErrorState, never a badge+table.
function MarketCardInner({ m, maxTurnover, maxVolume, maxRange, history }) {
  if (isStaleLiquidity(m?.provenance)) {
    return (
      <article
        className="min-w-0 rounded border border-term-border bg-term-bg p-3"
        aria-label={`${m?.label || m?.mic} liquidity unavailable`}
      >
        <div className="flex items-center justify-between gap-2">
          <h3 className="min-w-0 truncate text-sm font-bold text-term-text">{m?.label || m?.mic}</h3>
        </div>
        <div className="mt-2">
          <ErrorState
            title="Market data unavailable"
            detail="Market data unavailable (no live feed). Retry."
          />
        </div>
      </article>
    );
  }
  const total = m.total > 0 ? m.total : m.advancers + m.decliners + m.unchanged;
  const avg = formatSignedPct(m.avg_change_pct);
  const stateEntries = Object.entries(m.market_state_counts ?? {});
  const currency = m.currency ?? MARKET_CURRENCIES[m.mic] ?? "USD";
  let cardState = "delayed";
  try {
    cardState = deriveMarketCardState(m);
  } catch {
    cardState = "delayed";
  }
  return (
    <article
      className="min-w-0 rounded border border-term-border bg-term-bg p-3"
      aria-label={`${m.label || m.mic} liquidity and breadth`}
    >
      <div className="flex items-center justify-between gap-2">
        <h3 className="min-w-0 truncate text-sm font-bold text-term-text">{m.label || m.mic}</h3>
        <span className="shrink-0 text-[10px] text-term-muted">n={total}</span>
      </div>
      <div className="mt-2">
        <MarketStateBadge state={cardState} mic={m.mic} provenance={m.provenance} />
      </div>
      <div className="mt-2">
        <BreadthBar
          advancers={m.advancers}
          decliners={m.decliners}
          unchanged={m.unchanged}
          total={total}
          mic={m.mic}
        />
      </div>
      <dl className="mt-2 space-y-2 text-xs">
        <div className="flex items-center justify-between gap-2">
          <dt className="text-term-muted">Avg change</dt>
          <dd className={`font-bold ${avg.tone}`}>{avg.text}</dd>
        </div>
        <NativeMeter
          value={m.turnover}
          max={maxTurnover}
          currency={currency}
          label="Turnover"
          note={m.turnover_note ?? undefined}
        />
        {m.turnover_note ? (
          <p className="text-[10px] text-term-muted" role="note">
            {m.turnover_note}
          </p>
        ) : (
          <p className="text-[10px] text-term-muted" role="note">
            Turnover sums native price×volume with no FX conversion — cross-currency totals aren&apos;t
            comparable.
          </p>
        )}
        <NativeMeter value={m.total_volume} max={maxVolume} currency="shares" label="Volume" />
        <RangeBar value={m.avg_range_pct} max={maxRange} label="Avg range" />
      </dl>
      <div className="mt-2">
        <LiquiditySparkline history={history} market={m} mic={m.mic} />
      </div>
      <div className="mt-2 flex flex-wrap gap-1" aria-label={`${m.mic} market states`}>
        {stateEntries.length === 0 && (
          <span className="text-[10px] text-term-muted">no state data</span>
        )}
        {stateEntries.map(([state, count]) => (
          <span
            key={state}
            className="rounded border border-term-border px-1.5 py-0.5 text-[10px] text-term-muted"
            title={`${count} instrument(s) with market_state=${state}`}
          >
            {state}×{count}
          </span>
        ))}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <FreshnessBadge p={m.provenance} />
        <ProvenanceBadge p={m.provenance} />
      </div>
    </article>
  );
}

const MarketCard = memo(MarketCardInner);

function MarketCardWithGraphs({ m, maxTurnover, maxVolume, maxRange }) {
  const [expanded, setExpanded] = useState(false);
  const detail = useMarketDetail(m.mic, expanded);
  const histQ = useMarketLiquidityHistory(m.mic, "1D", true);
  const rows = detail.data?.rows?.length ? detail.data.rows : (m.rows ?? []);
  return (
    <div className="min-w-0">
      <MarketCard
        m={m}
        maxTurnover={maxTurnover}
        maxVolume={maxVolume}
        maxRange={maxRange}
        history={histQ.data}
      />
      <button
        type="button"
        className="term-btn-ghost mt-1 text-xs"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        aria-label={`${expanded ? "Hide" : "Show"} ${m.mic} symbol graphs`}
      >
        {expanded ? "▾ HIDE GRAPHS" : "▸ SHOW GRAPHS"}
      </button>
      {expanded && (
        <div className="mt-2">
          {detail.isLoading && <Skeleton label={`loading ${m.mic} symbols…`} lines={3} />}
          {detail.isError && (
            <ErrorState
              title={`${m.mic} detail unavailable`}
              detail={
                detail.error instanceof Error
                  ? detail.error.message
                  : "Per-symbol endpoint unreachable."
              }
              onRetry={() => void detail.refetch()}
            />
          )}
          {!detail.isLoading && !detail.isError && <MarketDetailGraphs rows={rows} />}
        </div>
      )}
    </div>
  );
}

function MarketLiquidityPanel({ data, isLoading, isError, error, onRetry }) {
  if (isLoading) {
    return (
      <section
        className="term-panel min-w-0 p-4"
        aria-labelledby="home-liquidity"
      >
        <h2 id="home-liquidity" className="term-label">
          Market liquidity &amp; breadth
        </h2>
        <div className="mt-2">
          <Skeleton label="loading market liquidity…" lines={6} />
        </div>
      </section>
    );
  }
  if (isError) {
    return (
      <section
        className="term-panel min-w-0 p-4"
        aria-labelledby="home-liquidity"
      >
        <h2 id="home-liquidity" className="term-label">
          Market liquidity &amp; breadth
        </h2>
        <div className="mt-2">
          <ErrorState title="Market liquidity unavailable" detail={errorMessage(error)} onRetry={onRetry} />
        </div>
      </section>
    );
  }
  const markets = data?.markets ?? [];
  if (markets.length === 0) {
    return (
      <section
        className="term-panel min-w-0 p-4"
        aria-labelledby="home-liquidity"
      >
        <h2 id="home-liquidity" className="term-label">
          Market liquidity &amp; breadth
        </h2>
        <div className="mt-2">
          <EmptyState
            title="No market breadth yet"
            detail="The screener returned no rows for XNYS / XNAS / XSHG / XPAR / XAMS / XBRU."
            actionLabel={onRetry ? "Retry" : undefined}
            onAction={onRetry}
          />
        </div>
      </section>
    );
  }
  let maxTurnover = 1;
  let maxVolume = 1;
  let maxRange = 0.5;
  for (const mk of markets) {
    if (typeof mk.turnover === "number" && Number.isFinite(mk.turnover) && mk.turnover > maxTurnover) {
      maxTurnover = mk.turnover;
    }
    if (
      typeof mk.total_volume === "number" &&
      Number.isFinite(mk.total_volume) &&
      mk.total_volume > maxVolume
    ) {
      maxVolume = mk.total_volume;
    }
    if (
      typeof mk.avg_range_pct === "number" &&
      Number.isFinite(mk.avg_range_pct) &&
      mk.avg_range_pct > maxRange
    ) {
      maxRange = mk.avg_range_pct;
    }
  }
  return (
    <section
      className="term-panel min-w-0 p-4"
      aria-labelledby="home-liquidity"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="home-liquidity" className="term-label">
          Market liquidity &amp; breadth
        </h2>
        {data && <FreshnessBadge p={data.provenance} />}
      </div>
      <div className="mt-3">
        <CrossMarketChart markets={markets} />
      </div>
      <div className="mt-3 grid min-w-0 gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {markets.map((m) => (
          <MarketCardWithGraphs
            key={m.mic}
            m={m}
            maxTurnover={maxTurnover}
            maxVolume={maxVolume}
            maxRange={maxRange}
          />
        ))}
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        {data && <ProvenanceBadge p={data.provenance} />}
      </div>
      <p className="mt-2 text-[10px] text-term-muted">
        Breadth = advancers/decliners from latest screener change_pct per MIC. Turnover in native
        currency (no FX) — cross-currency totals aren&apos;t comparable. Not investment advice.
      </p>
    </section>
  );
}

export { MarketLiquidityPanel as default };
