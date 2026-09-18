import React, { memo, useCallback, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getAuditForecasts, getProvidersHealth, getQuote } from "../api/client";
import { getTopSignals } from "../api/signals";
import { getNews } from "../api/news";
import AdSlot from "../components/AdSlot";
import { useAuth } from "../hooks/useAuth";
import StatusPill from "../components/StatusPill";
import MarketLiquidityPanel from "../components/MarketLiquidityPanel";
import MarketIndicesSection from "../components/AspiChart";
import LiquidationSection from "../components/LiquidationPanel";
import MarketStatusStrip from "../components/MarketStatusStrip";
import CollapsibleSection from "../components/CollapsibleSection";
import TopSignals from "../components/TopSignals";
import NewsPanel from "../components/NewsPanel";
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

const MAX_HOME_WATCHLIST = 10;
const MAX_HOME_REPORTS = 8;

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
            DETAILS →
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
      <div className="mt-1">
        <Link to={`/security/${encodeURIComponent(symbol)}`} className="text-[11px] text-term-green hover:underline">
          Why is it moving? See plain-English forecast →
        </Link>
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
        <StatusPill freshness={d.provenance} marketState={d.market_state} provenance={d.provenance} size="sm" />
      </div>
    </li>
  );
}
const WatchlistRow = memo(WatchlistRowInner);

function HomePage() {
  const navigate = useNavigate();
  const [signalHorizon, setSignalHorizon] = useState(21);
  // Tier mirror for the single in-feed ad below (guest-safe, zero requests).
  const { tier } = useAuth();
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
  const signals = useQuery({
    queryKey: ["signals-top", signalHorizon],
    queryFn: ({ signal }) => getTopSignals(signalHorizon, 5, { signal }),
    retry: false,
    staleTime: 60000,
  });
  const news = useQuery({
    queryKey: ["market-news"],
    queryFn: ({ signal }) => getNews("", 20, { signal }),
    retry: false,
    staleTime: 300000,
  });
  const liquidity = useMarketLiquidity();
  const { symbols: watchlist, add: addWatchSymbol, remove: removeWatchSymbol } = useWatchlist();
  const [draft, setDraft] = useState("");
  const [heroSearch, setHeroSearch] = useState("");
  function addSymbol() {
    const sym = normalizeSymbolInput(draft);
    if (!sym) return;
    addWatchSymbol(sym, "manual");
    setDraft("");
  }
  const removeSymbol = useCallback((sym) => removeWatchSymbol(sym), [removeWatchSymbol]);
  const showProviders = useMemo(() => providers.data ?? [], [providers.data]);
  const reports = useMemo(() => research.data?.forecasts ?? [], [research.data]);
  const providerIssueCount = useMemo(
    () => showProviders.filter((p) => p.status !== "ok" || p.circuit === "open").length,
    [showProviders]
  );

  function heroSubmit(e) {
    e.preventDefault();
    const sym = normalizeSymbolInput(heroSearch);
    if (!sym) return;
    navigate(`/security/${encodeURIComponent(sym)}`);
  }

  return (
    <div className="grid max-w-full gap-6">
      {/* Hero — plain-English welcome for non-traders */}
      <section className="term-panel-hero overflow-hidden p-6" aria-labelledby="home-hero">
        <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
          <div className="min-w-0 max-w-2xl">
            <p className="term-label">New here? Start in 30 seconds 👇</p>
            <h1 id="home-hero" className="mt-1 text-2xl font-black text-term-text md:text-3xl">
              Investing, explained <span className="text-term-green">simply</span>.
            </h1>
            <p className="mt-2 text-sm text-term-muted">
              Type any company below — we show the price, whether models lean <b>up or down</b>, and <b>why in plain words</b>.
              Every number shows its source and age. Nothing here is financial advice.
            </p>
            <form onSubmit={heroSubmit} className="mt-3 flex max-w-lg gap-2" role="search" aria-label="Look up a stock">
              <input
                className="term-input min-w-0 flex-1 text-base"
                value={heroSearch}
                onChange={(e) => setHeroSearch(e.target.value)}
                placeholder="Try AAPL, TSLA, 600519.SS, MC.PA…"
                aria-label="Look up a stock by ticker"
                spellCheck={false}
              />
              <button className="term-btn shrink-0" type="submit">
                EXPLAIN →
              </button>
            </form>
            <div className="mt-3 flex flex-wrap gap-4 text-xs text-term-muted">
              <span><b className="text-term-green">1.</b> Find a stock</span>
              <span><b className="text-term-green">2.</b> Read the plain-English forecast</span>
              <span><b className="text-term-green">3.</b> Follow it in My List</span>
            </div>
          </div>
          <div className="grid shrink-0 grid-cols-3 gap-2 text-center md:w-64">
            <div className="term-panel-nested p-3">
              <p className="text-lg font-black text-term-green">1–21D</p>
              <p className="text-[11px] text-term-muted">Short forecasts you can check</p>
            </div>
            <div className="term-panel-nested p-3">
              <p className="text-lg font-black text-term-green">6</p>
              <p className="text-[11px] text-term-muted">Markets, one search</p>
            </div>
            <div className="term-panel-nested p-3">
              <p className="text-lg font-black text-term-green">100%</p>
              <p className="text-[11px] text-term-muted">Numbers show sources</p>
            </div>
          </div>
        </div>
      </section>

      {/* Market open/closed status — always visible */}
      <MarketStatusStrip />

      {/* Top signals — ensemble v2 + news. The "what to buy/sell" shortlist. */}
      <TopSignals
        data={signals.data ?? null}
        isLoading={signals.isLoading}
        isError={signals.isError}
        error={signals.error}
        horizon={signalHorizon}
        onHorizon={setSignalHorizon}
        onRetry={() => void signals.refetch()}
      />

      {/* V2 compliant ads: one in-feed slot between TopSignals and the
          watchlist/news grid (slot index 2 of the per-tier budget). Never
          inside a collapsed CollapsibleSection. */}
      <AdSlot slotId="home-infeed" format="in-feed" tier={tier} slotIndex={2} />

      {/* My List (watchlist, separate) + Market news side by side */}
      <div className="grid min-w-0 items-start gap-6 lg:grid-cols-2">
        <section className="term-panel-hero flex min-w-0 flex-col p-4" aria-labelledby="home-watchlist">
          <div className="flex items-center justify-between gap-2">
            <div>
              <h2 id="home-watchlist" className="text-base font-extrabold text-term-text">
                ⭐ My List
              </h2>
              <p className="text-[11px] text-term-muted">Stocks you follow — saved in this browser. Separate from the ideas above.</p>
            </div>
            <Link to="/watchlist" className="term-btn-sm shrink-0">
              OPEN FULL LIST →
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
              placeholder="Add a ticker to follow (e.g. NVDA)"
              aria-label="Add symbol to watchlist"
              spellCheck={false}
            />
            <button className="term-btn shrink-0" type="submit">
              + FOLLOW
            </button>
          </form>
          {watchlist.length === 0 ? (
            <div className="mt-2">
              <EmptyState title="Nothing followed yet" detail="Tap + FOLLOW above — e.g. AAPL — and it will appear here with live prices." />
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
                open full list →
              </Link>
            </p>
          )}
        </section>

        <section className="term-panel min-w-0 p-4" aria-labelledby="home-news">
          <div className="flex items-center justify-between gap-2">
            <div>
              <h2 id="home-news" className="text-base font-extrabold text-term-text">
                📰 Market news
              </h2>
              <p className="text-[11px] text-term-muted">What is moving US stocks right now (Alpaca News).</p>
            </div>
            <button className="term-btn-sm shrink-0" type="button" onClick={() => void news.refetch()}>
              REFRESH
            </button>
          </div>
          <div className="mt-2">
            <NewsPanel
              data={news.data ?? null}
              isLoading={news.isLoading}
              isError={news.isError}
              error={news.error}
              onRetry={() => void news.refetch()}
            />
          </div>
        </section>
      </div>

      {/* Collapsible deep-dives */}
      <CollapsibleSection
        id="home-aspi"
        title="📈 Market indexes (ASPI & friends)"
        subtitle="One line per market — are prices in general going up or down? Click SHOW to expand."
        defaultOpen={false}
      >
        <MarketIndicesSection />
      </CollapsibleSection>

      <CollapsibleSection
        id="home-liquidity"
        title="💧 Market activity (liquidity)"
        subtitle="How busy is each market? Busy usually means easier to buy/sell. Click SHOW to expand."
        defaultOpen={false}
      >
        <MarketLiquidityPanel
          data={liquidity.data ?? null}
          isLoading={liquidity.isLoading}
          isError={liquidity.isError}
          error={liquidity.error}
          onRetry={() => void liquidity.refetch()}
        />
        <div className="mt-4">
          <LiquidationSection />
        </div>
      </CollapsibleSection>

      <CollapsibleSection
        id="home-research"
        title="🔬 Latest research"
        subtitle="Recent forecasts our system published. Click a row to see the full plain-English explanation."
        defaultOpen={false}
        badge={<span className="term-btn-sm">{reports.length} reports</span>}
      >
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
            No reports yet. Forecasts you run will appear here.
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
                    {r.horizon_days ? ` · ${r.horizon_days}d forecast` : ""}
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
      </CollapsibleSection>

      <CollapsibleSection
        id="home-providers"
        title="🔌 Data health"
        subtitle="Where do our numbers come from, and is each source working? Green = healthy."
        defaultOpen={false}
        badge={
          providers.data
            ? providerIssueCount === 0
              ? <span className="term-btn-sm">● ALL HEALTHY</span>
              : <span className="term-btn-sm">● {providerIssueCount} ISSUE{providerIssueCount === 1 ? "" : "S"}</span>
            : null
        }
      >
        {providers.isLoading && (
          <div className="mt-2">
            <Skeleton label="loading provider health…" lines={3} />
          </div>
        )}
        {providers.isError && (
          <div className="mt-2">
            <ErrorState
              title="Data health unavailable"
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
              const noSamples = (latency === void 0 || latency === null) && (p.total_calls ?? 0) === 0;
              return (
                <li key={p.name} className="flex justify-between gap-1.5 border-b border-term-border py-2">
                  <span className="min-w-0 truncate">{p.name}</span>
                  <span className={`term-num ${bad ? "text-term-red" : "text-term-green"}`} title="Provider health: working = last call ok; circuit closed = connection healthy (not market state), open = requests paused">
                    {bad ? "● needs attention" : "● working"}
                    {p.circuit ? ` · circuit ${p.circuit}` : ""}
                    {latency !== void 0 && latency !== null ? ` · ${latency}ms` : noSamples ? " · no data yet" : ""}
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
        <p className="mt-2 text-[11px] text-term-muted">
          Plain English: green sources are sending fresh prices. If one shows an issue, its stocks may be slower to update — other sources cover for it.
        </p>
      </CollapsibleSection>
    </div>
  );
}
export { HomePage as default };
