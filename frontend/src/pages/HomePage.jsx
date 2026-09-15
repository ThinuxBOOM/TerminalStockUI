import React, { memo, useCallback, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getAuditForecasts, getProvidersHealth, getQuote } from "../api/client";
import ProvenanceBadge from "../components/ProvenanceBadge";
import FreshnessBadge from "../components/FreshnessBadge";
import MarketStateBadge from "../components/MarketStateBadge";
import MarketLiquidityPanel from "../components/MarketLiquidityPanel";
import MarketIndicesSection from "../components/AspiChart";
import LiquidationSection from "../components/LiquidationPanel";
import MarketStatusStrip from "../components/MarketStatusStrip";
import { useMarketLiquidity } from "../hooks/useMarketLiquidity";
import useWatchlist from "../hooks/useWatchlist";
import CurrencyValue from "../components/CurrencyValue";
import { formatPct1 } from "../utils/format";
import Skeleton from "../components/Skeleton";
import EmptyState from "../components/EmptyState";
import ErrorState from "../components/ErrorState";

function normalizeSymbolInput(v) {
  return v.trim().toUpperCase().replace(/\s+/g, "");
}

const MAX_HOME_WATCHLIST = 50;
const MAX_HOME_REPORTS = 20;

// Memoized: props are primitives, so parent re-renders (draft keystrokes)
// skip these rows entirely.
function WatchlistRowInner({ symbol, onRemove }) {
  const q = useQuery({
    queryKey: ["quote", symbol],
    queryFn: ({ signal }) => getQuote(symbol, undefined, { signal }),
    retry: false,
    staleTime: 30000,
  });
  if (q.isLoading) {
    return (
      <li className="p-3" role="status" aria-label={`loading ${symbol}`}>
        <Skeleton label={`loading ${symbol}…`} lines={1} />
      </li>
    );
  }
  if (q.isError || !q.data) {
    return (
      <li className="flex items-center justify-between gap-2 p-3 text-xs">
        <span className="min-w-0 truncate text-term-muted">{symbol} — unavailable</span>
        <span className="flex shrink-0 gap-2">
          <Link className="text-term-green" to={`/security/${encodeURIComponent(symbol)}`}>
            BRIEF →
          </Link>
          <button
            type="button"
            className="text-term-muted hover:text-term-red"
            onClick={() => onRemove(symbol)}
            aria-label={`Remove ${symbol} from watchlist`}
          >
            ✕
          </button>
        </span>
      </li>
    );
  }
  const d = q.data;
  return (
    <li className="p-3">
      <div className="flex items-center justify-between gap-2 text-sm">
        <Link
          to={`/security/${encodeURIComponent(symbol)}`}
          className="min-w-0 truncate font-bold text-term-green hover:underline"
        >
          {d.symbol} · <CurrencyValue value={d.price} currency={d.currency ?? "USD"} />
        </Link>
        <span className="flex shrink-0 items-center gap-2">
          <FreshnessBadge p={d.provenance} />
          <button
            type="button"
            className="text-xs text-term-muted hover:text-term-red"
            onClick={() => onRemove(symbol)}
            aria-label={`Remove ${symbol} from watchlist`}
          >
            ✕
          </button>
        </span>
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-2">
        <MarketStateBadge state={d.market_state} provenance={d.provenance} />
        <ProvenanceBadge p={d.provenance} />
      </div>
    </li>
  );
}
const WatchlistRow = memo(WatchlistRowInner);

// Homepage dashboard. Single-column stack of full-width sections — no
// multi-column grid with uneven children, so there are no holes: the old
// 3-col grid left a two-column gap beside the short Market-status card.
// The only 3-column row (watchlist / providers / research) has exactly
// three equal children that stretch to equal height.
function HomePage() {
  const providers = useQuery({
    queryKey: ["providers-health"],
    queryFn: getProvidersHealth,
    retry: false,
    staleTime: 30000,
  });
  const research = useQuery({
    queryKey: ["audit-forecasts", "recent"],
    queryFn: () => getAuditForecasts(5),
    retry: false,
    staleTime: 60000,
  });
  const liquidity = useMarketLiquidity();
  const { symbols: watchlist, add: addWatchSymbol, remove: removeWatchSymbol } = useWatchlist();
  const [draft, setDraft] = useState("");
  function addSymbol() {
    const sym = normalizeSymbolInput(draft);
    if (!sym) return;
    addWatchSymbol(sym, "manual");
    setDraft("");
  }
  const removeSymbol = useCallback((sym) => removeWatchSymbol(sym), [removeWatchSymbol]);
  // Fail-closed: provider rows come from GET /api/providers/health only.
  // No fallback mapping from /health, no cached-values banner — an
  // unreachable endpoint is an explicit error with retry.
  const showProviders = useMemo(() => providers.data ?? [], [providers.data]);
  const reports = useMemo(() => research.data?.forecasts ?? [], [research.data]);
  return (
    <div className="grid max-w-full gap-4">
      <MarketStatusStrip />
      <MarketLiquidityPanel
        data={liquidity.data ?? null}
        isLoading={liquidity.isLoading}
        isError={liquidity.isError}
        error={liquidity.error}
        onRetry={() => void liquidity.refetch()}
      />
      <MarketIndicesSection />
      <LiquidationSection />
      <div className="grid min-w-0 items-stretch gap-4 md:grid-cols-3">
        <section className="term-panel flex min-w-0 flex-col p-4" aria-labelledby="home-watchlist">
          <div className="flex items-center justify-between gap-2">
            <h2 id="home-watchlist" className="term-label">
              Watchlist
            </h2>
            <Link to="/watchlist" className="text-xs text-term-green">
              ALL →
            </Link>
          </div>
          <form
            className="mt-2 flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              addSymbol();
            }}
          >
            <input
              className="term-input min-w-0 flex-1"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="Add symbol (e.g. MC.PA)"
              aria-label="Add symbol to watchlist"
              spellCheck={false}
            />
            <button className="term-btn shrink-0" type="submit">
              ADD
            </button>
          </form>
          {watchlist.length === 0 ? (
            <div className="mt-2">
              <EmptyState title="Watchlist is empty" detail="Add a symbol above — it persists in this browser." />
            </div>
          ) : (
            <ul className="mt-2 divide-y divide-term-border">
              {watchlist.slice(0, MAX_HOME_WATCHLIST).map((s) => (
                <WatchlistRow key={s} symbol={s} onRemove={removeSymbol} />
              ))}
            </ul>
          )}
          {watchlist.length > MAX_HOME_WATCHLIST && (
            <p className="mt-1 text-[11px] text-term-muted" role="status">
              showing first {MAX_HOME_WATCHLIST} of {watchlist.length} —{" "}
              <Link to="/watchlist" className="text-term-green">
                open full watchlist →
              </Link>
            </p>
          )}
        </section>
        <section className="term-panel min-w-0 p-4" aria-labelledby="home-provider-health">
          <h2 id="home-provider-health" className="term-label">
            Provider health
          </h2>
          {providers.isLoading && (
            <div className="mt-2">
              <Skeleton label="loading provider health…" lines={3} />
            </div>
          )}
          {providers.isError && (
            <div className="mt-2">
              <ErrorState
                title="Provider health unavailable"
                detail={
                  providers.error instanceof Error
                    ? providers.error.message
                    : "Backend /api/providers/health unreachable."
                }
                onRetry={() => void providers.refetch()}
              />
            </div>
          )}
          {showProviders.length > 0 ? (
            <ul className="mt-2 space-y-1 text-xs">
              {showProviders.map((p) => {
                const latency = p.latency_p50_ms ?? p.latency_ms ?? p.latency_p95_ms;
                const bad = p.status !== "ok" || (p.circuit !== undefined && p.circuit === "open");
                return (
                  <li key={p.name} className="flex justify-between gap-2 border-b border-term-border pb-1">
                    <span className="min-w-0 truncate">{p.name}</span>
                    <span className={bad ? "text-term-red" : "text-term-green"}>
                      {p.status}
                      {p.circuit ? ` · ${p.circuit}` : ""}
                      {latency !== undefined ? ` · ${latency}ms` : ""}
                    </span>
                  </li>
                );
              })}
            </ul>
          ) : (
            !providers.isLoading &&
            !providers.isError && (
              <p className="mt-2 text-xs text-term-muted" role="status">
                No provider data — the health endpoint returned no rows.
              </p>
            )
          )}
        </section>
        <section className="term-panel min-w-0 p-4" aria-labelledby="home-research">
          <h2 id="home-research" className="term-label">
            Latest research
          </h2>
          {research.isLoading && (
            <div className="mt-2">
              <Skeleton label="loading latest research…" lines={3} />
            </div>
          )}
          {research.isError && (
            <div className="mt-2">
              <ErrorState
                title="Research feed unavailable"
                detail={research.error instanceof Error ? research.error.message : "Backend /api/audit/forecasts unreachable."}
                onRetry={() => void research.refetch()}
              />
            </div>
          )}
          {!research.isLoading && !research.isError && reports.length === 0 && (
            <p className="mt-1 text-xs text-term-muted">
              No reports yet. Scheduled reports land here (Milestone 4+).
            </p>
          )}
          {!research.isLoading && !research.isError && reports.length > 0 && (
            <ul className="mt-2 space-y-1 text-xs">
              {reports.slice(0, MAX_HOME_REPORTS).map((r, i) => (
                <li
                  key={r.forecast_id ?? `${r.symbol ?? "unknown"}-${i}`}
                  className="flex items-center justify-between gap-2 border-b border-term-border pb-1"
                >
                  {r.symbol ? (
                    <Link
                      to={`/security/${encodeURIComponent(r.symbol)}`}
                      className="min-w-0 truncate font-bold text-term-green hover:underline"
                    >
                      {r.symbol}
                      {r.horizon_days ? ` · ${r.horizon_days}d` : ""}
                    </Link>
                  ) : (
                    <span className="min-w-0 truncate text-term-muted">
                      —{r.horizon_days ? ` · ${r.horizon_days}d` : ""}
                    </span>
                  )}
                  <span className="shrink-0 text-term-muted">{formatPct1(r.direction_probability)}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
export { HomePage as default };
