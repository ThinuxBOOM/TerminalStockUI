import React, { useMemo } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getProvidersHealth } from "../api/client";
import { getModelCard } from "../api/forecast";
import { getNews } from "../api/news";
import useWatchlist from "../hooks/useWatchlist";
import { useAuth } from "../hooks/useAuth";
import Card from "../components/ui/Card";
import Stat from "../components/ui/Stat";
import StatusDot from "../components/ui/StatusDot";
import CollapsibleSection from "../components/CollapsibleSection";
import MarketStatusStrip from "../components/MarketStatusStrip";
import MarketLiquidityPanel from "../components/MarketLiquidityPanel";
import NewsPanel from "../components/NewsPanel";
import IndexTiles from "../components/home/IndexTiles";
import SignalsTable from "../components/home/SignalsTable";
import WatchlistTable from "../components/home/WatchlistTable";
import { useMarketLiquidity } from "../hooks/useMarketLiquidity";
import { fmtNum, fmtPct } from "../utils/format";

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

function ModelRecordCard() {
  const q = useQuery({ queryKey: ["model-card"], queryFn: ({ signal }) => getModelCard({ signal }), staleTime: 3600000, retry: false });
  const r = q.data?.report?.horizons?.["21"] ?? {};
  return (
    <Card title="Model record" subtitle="21-day horizon, walk-forward 2021–2026" actions={<Link to="/model" className="term-link text-xs">Model Lab →</Link>}>
      {q.isLoading ? <div className="h-24 animate-pulse rounded-md bg-term-panel2" /> : (
        <div className="grid grid-cols-2 gap-x-4 gap-y-3">
          <Stat size="sm" label="Range coverage" value={fmtPct(r.range?.coverage_80, 1)} note="target 80%" />
          <Stat size="sm" label="Drop-risk skill" value={fmtPct(r.drop_risk?.skill, 1, { signed: true })} note="vs. base rate" />
          <Stat size="sm" label="Ranking IC" value={fmtNum(r.out?.ic_mean, 3)} note={`t = ${r.out?.ic_t ?? "—"}`} />
          <Stat size="sm" label="Model" value={q.data?.version?.replace("v4-", "") ?? "—"} note={q.data ? `${q.data.universe_size} stocks` : undefined} />
        </div>
      )}
    </Card>
  );
}

const AI_PROVIDERS = new Set(["anthropic", "openai", "gemini", "xai"]);

function DataHealthCard() {
  const q = useQuery({ queryKey: ["providers-health"], queryFn: getProvidersHealth, retry: false, staleTime: 30000 });
  const rows = q.data ?? [];
  const issues = rows.filter((p) => p.status !== "ok" && p.status !== "unknown");
  return (
    <Card title="Data health" actions={<Link to="/providers" className="term-link text-xs">Details →</Link>}>
      {q.isLoading ? <div className="h-16 animate-pulse rounded-md bg-term-panel2" /> : (
        <ul className="space-y-1.5 text-sm">
          {rows.filter((p) => !AI_PROVIDERS.has(String(p.name).toLowerCase())).slice(0, 6).map((p) => (
            <li key={p.name} className="flex items-center justify-between">
              <span className="text-term-text">{p.name}</span>
              <StatusDot state={p.status === "ok" ? "fresh" : p.status === "unknown" ? "neutral" : "error"} label={p.status === "unknown" ? "idle" : p.status} />
            </li>
          ))}
          {!rows.length ? <li className="text-term-muted">No provider data.</li> : null}
        </ul>
      )}
      {issues.length ? <p className="mt-2 text-xs text-term-amber">{issues.length} source{issues.length > 1 ? "s" : ""} need attention.</p> : null}
    </Card>
  );
}

function HomePage() {
  const { user } = useAuth();
  const { symbols, remove } = useWatchlist();
  const news = useQuery({ queryKey: ["market-news"], queryFn: ({ signal }) => getNews("", 12, { signal }), retry: false, staleTime: 300000 });
  const liquidity = useMarketLiquidity();
  const name = useMemo(() => (user?.email ? user.email.split("@")[0] : ""), [user]);
  const today = new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });
  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <p className="text-xs text-term-muted">{today}</p>
          <h1 className="type-page">{greeting()}{name ? `, ${name}` : ""}</h1>
        </div>
        <MarketStatusStrip />
      </header>

      <IndexTiles />

      <div className="grid min-w-0 gap-4 xl:grid-cols-[minmax(0,1fr)_22rem]">
        <SignalsTable />
        <div className="space-y-4">
          <ModelRecordCard />
          <DataHealthCard />
        </div>
      </div>

      <Card title="Watchlist" subtitle={`${symbols.length} stock${symbols.length === 1 ? "" : "s"} · ranks and ranges at 21 days`} actions={<Link to="/watchlist" className="term-link text-xs">Open →</Link>} pad={false}>
        <WatchlistTable symbols={symbols.slice(0, 12)} onRemove={remove} />
      </Card>

      <div className="grid min-w-0 gap-4 lg:grid-cols-2">
        <Card title="Market news" subtitle="US market headlines" actions={<button type="button" className="term-btn-sm" onClick={() => void news.refetch()}>Refresh</button>}>
          <NewsPanel data={news.data ?? null} isLoading={news.isLoading} isError={news.isError} error={news.error} onRetry={() => void news.refetch()} />
        </Card>
        <CollapsibleSection id="home-liquidity" title="Market activity" subtitle="Turnover and breadth by venue" defaultOpen>
          <MarketLiquidityPanel data={liquidity.data ?? null} isLoading={liquidity.isLoading} isError={liquidity.isError} error={liquidity.error} onRetry={() => void liquidity.refetch()} />
        </CollapsibleSection>
      </div>
    </div>
  );
}

export { HomePage as default };
