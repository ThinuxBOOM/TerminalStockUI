import React, { Suspense, lazy, useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { Star } from "lucide-react";
import {
  SUPPORTED_INDICATORS,
  TIMEFRAME_PRESETS,
  extractBackendDetail,
  getAnalytics,
  getChart,
  loadFavoriteIndicators,
  resolveTimeframePreset,
  saveFavoriteIndicators,
} from "../../api/client";
import { getForecastAll } from "../../api/forecast";
import { getSymbolNews } from "../../api/news";
import { getSymbolRisk } from "../../api/risk";
import useWatchlist from "../../hooks/useWatchlist";
import Card from "../../components/ui/Card";
import Badge from "../../components/ui/Badge";
import Delta from "../../components/ui/Delta";
import Segmented from "../../components/ui/Segmented";
import Tabs from "../../components/ui/Tabs";
import StatusPill from "../../components/StatusPill";
import ErrorState from "../../components/ErrorState";
import { fmtMoney, formatDateTime } from "../../utils/format";
import { ForecastSummaryCard, ForecastTab } from "./ForecastPanels";
import { RiskSummaryCard, RiskTab } from "./RiskPanels";
import { AITab, FundamentalsTab, NewsTab } from "./ResearchPanels";

const PriceChart = lazy(() => import("./PriceChart"));

const TABS = [
  { id: "overview", label: "Overview" },
  { id: "forecast", label: "Forecast" },
  { id: "risk", label: "Risk" },
  { id: "fundamentals", label: "Fundamentals" },
  { id: "news", label: "News" },
  { id: "ai", label: "AI" },
];
const TIMEFRAMES = TIMEFRAME_PRESETS.filter((p) => p.id !== "1D").map((p) => ({ value: p.id, label: p.label, title: p.note }));

function eventsFromAnalytics(a) {
  const raw = Array.isArray(a?.events) ? a.events : [];
  return raw
    .map((e) => (typeof e === "string" ? { date: "", title: e } : { date: String(e?.date ?? e?.ts ?? "").trim(), title: String(e?.title ?? e?.event ?? e?.name ?? "").trim() }))
    .filter((e) => e.title)
    .slice(0, 20);
}

function SecurityPage({ symbol }) {
  const [params, setParams] = useSearchParams();
  const tab = TABS.some((t) => t.id === params.get("tab")) ? params.get("tab") : "overview";
  const setTab = (id) => setParams((p) => {
    const next = new URLSearchParams(p);
    if (id === "overview") next.delete("tab");
    else next.set("tab", id);
    return next;
  }, { replace: true });
  useEffect(() => {
    try {
      localStorage.setItem("onemarket.lastSecurity.v1", symbol);
    } catch {
      // the sidebar falls back to AAPL
    }
  }, [symbol]);
  const [horizon, setHorizon] = useState(21);
  const [timeframeId, setTimeframeId] = useState("1Y");
  const preset = resolveTimeframePreset(timeframeId);
  const [indicators, setIndicators] = useState(() => loadFavoriteIndicators(undefined, ["SMA50", "SMA200"]));
  const { symbols: watch, add: watchAdd, remove: watchRemove } = useWatchlist();
  const inWatch = useMemo(() => watch.map((s) => String(s).toUpperCase()).includes(symbol.toUpperCase()), [watch, symbol]);

  const chartQ = useQuery({
    queryKey: ["chart", symbol, preset.timeframe, preset.limit],
    queryFn: ({ signal }) => getChart(symbol, preset.timeframe, preset.limit, { signal }),
    retry: 1,
    staleTime: 120000,
  });
  const forecastQ = useQuery({
    queryKey: ["forecast-all", symbol],
    queryFn: ({ signal }) => getForecastAll(symbol, { signal }),
    retry: 1,
    staleTime: 300000,
  });
  const riskQ = useQuery({
    queryKey: ["risk", symbol],
    queryFn: ({ signal }) => getSymbolRisk(symbol, { signal }),
    retry: 1,
    staleTime: 600000,
  });
  const analyticsQ = useQuery({
    queryKey: ["analytics", symbol, indicators.join(",")],
    queryFn: ({ signal }) => getAnalytics(symbol, { signal, indicators }),
    retry: 1,
    staleTime: 300000,
  });
  const newsQ = useQuery({
    queryKey: ["news-symbol", symbol],
    queryFn: ({ signal }) => getSymbolNews(symbol, 15, { signal }),
    enabled: tab === "news",
    retry: false,
    staleTime: 300000,
  });

  const q = chartQ.data?.quote ?? null;
  const currency = q?.currency ?? null;
  const forecasts = forecastQ.data?.horizons ?? null;
  const forecastError = forecastQ.isError ? extractBackendDetail(forecastQ.error, "forecast unavailable") : null;
  const riskError = riskQ.isError ? extractBackendDetail(riskQ.error, "risk metrics unavailable") : null;
  const events = useMemo(() => eventsFromAnalytics(analyticsQ.data), [analyticsQ.data]);
  const company = q?.instrument?.company_name;
  const mic = q?.instrument?.exchange_mic;

  function toggleIndicator(name) {
    setIndicators((prev) => {
      const next = prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name];
      saveFavoriteIndicators(undefined, next);
      return next;
    });
  }

  return (
    <div className="min-w-0 space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-2xl font-semibold tracking-tight text-term-text">{symbol}</h1>
            {mic ? <Badge>{mic}</Badge> : null}
            {currency ? <Badge>{currency}</Badge> : null}
          </div>
          <p className="mt-0.5 truncate text-sm text-term-muted">{company && company !== symbol ? company : " "}</p>
        </div>
        <div className="flex flex-wrap items-end gap-4">
          {chartQ.isLoading && !q ? (
            <div className="h-12 w-48 animate-pulse rounded-md bg-term-panel" />
          ) : q ? (
            <div className="text-right">
              <div className="flex items-baseline justify-end gap-2">
                <span className="term-num text-3xl font-semibold tracking-tight text-term-text">{fmtMoney(q.price, currency)}</span>
                <Delta value={typeof q.change_pct === "number" ? q.change_pct / 100 : null} className="text-sm font-medium" />
              </div>
              <div className="mt-1 flex items-center justify-end gap-2 text-2xs text-term-muted">
                {q.price_time ? <span title="When this price was set on the exchange">price at {formatDateTime(q.price_time)}</span> : null}
                <StatusPill freshness={q.provenance} marketState={q.market_state} provenance={q.provenance} size="sm" />
              </div>
            </div>
          ) : null}
          <button
            type="button"
            className={inWatch ? "term-btn-ghost" : "term-btn"}
            aria-pressed={inWatch}
            onClick={() => (inWatch ? watchRemove(symbol) : watchAdd(symbol, "security"))}
          >
            <Star className={`h-4 w-4 ${inWatch ? "fill-term-amber text-term-amber" : ""}`} aria-hidden="true" />
            {inWatch ? "Watching" : "Watch"}
          </button>
        </div>
      </header>

      {chartQ.isError && !q ? (
        <ErrorState title="Price unavailable" detail={extractBackendDetail(chartQ.error, "quote endpoint unreachable")} onRetry={() => void chartQ.refetch()} />
      ) : null}

      <Tabs tabs={TABS} active={tab} onChange={setTab} ariaLabel={`${symbol} sections`} />

      <div role="tabpanel" aria-label={TABS.find((t) => t.id === tab)?.label} className="animate-fade-up">
        {tab === "overview" ? (
          <div className="grid min-w-0 gap-4 xl:grid-cols-[minmax(0,1fr)_22rem]">
            <Card
              title="Price"
              actions={
                <>
                  <Segmented options={TIMEFRAMES} value={timeframeId} onChange={setTimeframeId} ariaLabel="Chart timeframe" size="xs" />
                </>
              }
            >
              <div className="mb-3 flex flex-wrap gap-1.5" role="group" aria-label="Indicators">
                {SUPPORTED_INDICATORS.map((name) => {
                  const on = indicators.includes(name);
                  return (
                    <button
                      key={name}
                      type="button"
                      aria-pressed={on}
                      onClick={() => toggleIndicator(name)}
                      className={`rounded-full border px-2.5 py-0.5 text-2xs font-medium transition-colors ${on ? "border-term-accent/50 bg-term-accentDim text-term-accentHover" : "border-term-border text-term-muted hover:text-term-text"}`}
                    >
                      {name}
                    </button>
                  );
                })}
              </div>
              <Suspense fallback={<div className="h-80 animate-pulse rounded-md bg-term-panel2" />}>
                <PriceChart
                  symbol={symbol}
                  data={chartQ.data?.candles ?? null}
                  loading={chartQ.isLoading || chartQ.isFetching}
                  error={chartQ.isError ? extractBackendDetail(chartQ.error, "bars unavailable") : null}
                  provenance={chartQ.data?.provenance ?? null}
                  indicators={analyticsQ.data?.indicators ?? {}}
                  requestedIndicators={indicators}
                  indicatorsLoading={analyticsQ.isLoading || analyticsQ.isFetching}
                  indicatorsError={analyticsQ.isError ? extractBackendDetail(analyticsQ.error, "indicators unavailable") : null}
                  indicatorsProvenance={analyticsQ.data?.provenance ?? null}
                  currency={currency}
                  onRetryIndicators={() => void analyticsQ.refetch()}
                />
              </Suspense>
            </Card>
            <div className="min-w-0 space-y-4">
              <ForecastSummaryCard
                forecasts={forecasts}
                horizon={horizon}
                onHorizon={setHorizon}
                currency={currency}
                loading={forecastQ.isLoading}
                error={forecastError}
                onOpen={() => setTab("forecast")}
              />
              <RiskSummaryCard risk={riskQ.data} loading={riskQ.isLoading} onOpen={() => setTab("risk")} />
            </div>
          </div>
        ) : null}
        {tab === "forecast" ? (
          <ForecastTab forecasts={forecasts} horizon={horizon} onHorizon={setHorizon} currency={currency} loading={forecastQ.isLoading} error={forecastError} onRetry={() => void forecastQ.refetch()} />
        ) : null}
        {tab === "risk" ? (
          <RiskTab risk={riskQ.data} loading={riskQ.isLoading} error={riskError} onRetry={() => void riskQ.refetch()} price={q?.price} currency={currency} forecasts={forecasts} />
        ) : null}
        {tab === "fundamentals" ? (
          <FundamentalsTab analytics={analyticsQ.data} loading={analyticsQ.isLoading} error={analyticsQ.isError ? extractBackendDetail(analyticsQ.error, "analytics unavailable") : null} onRetry={() => void analyticsQ.refetch()} events={events} />
        ) : null}
        {tab === "news" ? (
          <NewsTab news={newsQ.data ?? null} loading={newsQ.isLoading} error={newsQ.isError ? newsQ.error : null} onRetry={() => void newsQ.refetch()} />
        ) : null}
        {tab === "ai" ? <AITab symbol={symbol} /> : null}
      </div>

    </div>
  );
}

export { SecurityPage as default };
