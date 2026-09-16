import React, { memo, useCallback, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getAuditForecasts, getProvidersHealth, getQuote } from "../api/client";
import StatusPill from "../components/StatusPill";
import MarketLiquidityPanel from "../components/MarketLiquidityPanel";
import MarketIndicesSection from "../components/AspiChart";
import LiquidationSection from "../components/LiquidationPanel";
import MarketStatusStrip from "../components/MarketStatusStrip";
import { useMarketLiquidity } from "../hooks/useMarketLiquidity";
import useWatchlist from "../hooks/useWatchlist";
import CurrencyValue from "../components/CurrencyValue";
import { changeArrow, changeColor, formatPct1 } from "../utils/format";
import Skeleton from "../components/Skeleton";
import EmptyState from "../components/EmptyState";
import ErrorState from "../components/ErrorState";

function normalizeSymbolInput(v) {
  return v.trim().toUpperCase().replace(/\s+/g, "");
}

function formatResearchDate(r) {
  const raw = r?.created_at ?? r?.target_date ?? null;
  if (typeof raw !== "string" || raw.trim() === "") return "—";
  const ms = Date.parse(raw);
  if (!Number.isFinite(ms)) return "—";
  try {
    return new Date(ms).toISOString().slice(0, 10);
  } catch {
    return String(raw).slice(0, 10);
  }
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
      <li className="p-2.5" role="status" aria-label={`loading ${symbol}`}>
        <Skeleton label={`loading ${symbol}…`} lines={1} />
      </li>
    );
  }
  if (q.isError || !q.data) {
    return (
      <li className="flex items-center justify-between gap-1.5 p-2.5 text-xs">
        <span className="min-w-0 truncate text-term-muted">{symbol} — unavailable</span>
        <span className="flex shrink-0 items-center gap-1.5">
          <Link className="text-term-green" to={`/security/${encodeURIComponent(symbol)}`}>
            BRIEF →
          </Link>
          <button
            type="button"
            className="term-icon-btn"
            onClick={() => onRemove(symbol)}
            aria-label="Remove from watchlist"
            title={`Remove ${symbol} from watchlist`}
          >
            ✕
          </button>
        </span>
      </li>
    );
  }
  const d = q.data;
  const chg = d.change_pct;
  return (
    <li className="p-2.5">
      <div className="flex items-center justify-between gap-1.5">
        <div className="text-2xs uppercase tracking-widest text-term-muted font-sans">
          <Link
            to={`/security/${encodeURIComponent(symbol)}`}
            className="hover:text-term-green hover:underline"
          >
            {d.symbol}
          </Link>
        </div>
        <button
          type="button"
          className="term-icon-btn"
          onClick={() => onRemove(symbol)}
          aria-label="Remove from watchlist"
          title={`Remove ${symbol} from watchlist`}
        >
          ✕
        </button>
      </div>
      <div className="mt-1.5 flex flex-wrap items-baseline gap-2">
        <span className="term-num text-display-sm font-bold text-term-text">
          <CurrencyValue value={d.price} currency={d.currency ?? "USD"} />
        </span>
        <span className={`term-num text-sm font-semibold ${changeColor(chg)}`}>
          {Number.isFinite(chg) ? `${changeArrow(chg)} ${formatPct1(Math.abs(chg) / 100)}` : "—"}
        </span>
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
        <StatusPill freshness={d.provenance} marketState={d.market_state} provenance={d.provenance} size="sm" />
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
    <div className="grid max-w-full gap-6">
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
      <div className="grid min-w-0 items-stretch gap-6 md:grid-cols-3">
        <section className="term-panel-hero flex min-w-0 flex-col p-4" aria-labelledby="home-watchlist">
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
            <ul className="mt-2 space-y-1.5 text-xs">
              {showProviders.map((p) => {
                const latency = p.latency_p50_ms ?? p.latency_ms ?? p.latency_p95_ms;
                const bad = p.status !== "ok" || (p.circuit !== undefined && p.circuit === "open");
                // No samples: say so (never a fabricated 0ms).
                const noSamples = (latency === void 0 || latency === null) && (p.total_calls ?? 0) === 0;
                return (
                  <li key={p.name} className="flex justify-between gap-1.5 border-b border-term-border py-2">
                    <span className="min-w-0 truncate">{p.name}</span>
                    <span className={`term-num ${bad ? "text-term-red" : "text-term-green"}`}>
                      {p.status}
                      {p.circuit ? ` · ${p.circuit}` : ""}
                      {latency !== void 0 && latency !== null ? ` · ${latency}ms` : noSamples ? " · no samples yet" : ""}
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
            <ul className="mt-2 space-y-1.5 text-xs">
              {reports.slice(0, MAX_HOME_REPORTS).map((r, i) => (
                <li
                  key={r.forecast_id ?? `${r.symbol ?? "unknown"}-${i}`}
                  className="flex items-center justify-between gap-1.5 border-b border-term-border py-2"
                >
                  {r.symbol ? (
                    <Link
                      to={`/security/${encodeURIComponent(r.symbol)}`}
                      className="min-w-0 truncate font-bold text-term-green hover:underline"
                      title={r.created_at ? `researched ${r.created_at}` : undefined}
                    >
                      {r.symbol}
                      {r.horizon_days ? ` · ${r.horizon_days}d` : ""}
                    </Link>
                  ) : (
                    <span className="min-w-0 truncate text-term-muted">
                      —{r.horizon_days ? ` · ${r.horizon_days}d` : ""}
                    </span>
                  )}
                  <span className="flex shrink-0 items-center gap-2">
                    <span className="term-num text-[11px] text-term-muted" title={r.created_at ?? r.target_date ?? "research date unavailable"}>
                      {formatResearchDate(r)}
                    </span>
                    <span className="term-num text-term-muted">{formatPct1(r.direction_probability)}</span>
                  </span>
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
