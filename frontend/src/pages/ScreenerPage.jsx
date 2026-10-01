import React, { useEffect, useMemo, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { ArrowDown, ArrowUp, Download, Search } from "lucide-react";
import { extractBackendDetail } from "../api/client";
import { getScreener } from "../api/screener";
import Card from "../components/ui/Card";
import Badge from "../components/ui/Badge";
import Segmented from "../components/ui/Segmented";
import { RangeBar, RankMeter } from "../components/charts/Inline";
import ErrorState from "../components/ErrorState";
import { fmtMoney, fmtNum, fmtPct, fmtRank } from "../utils/format";

const MARKETS = [
  { value: "US", label: "S&P 500" },
  { value: "ALL", label: "All" },
  { value: "XSHG", label: "Shanghai" },
  { value: "XPAR", label: "Paris" },
  { value: "XAMS", label: "Amsterdam" },
  { value: "XBRU", label: "Brussels" },
];
const HORIZONS = [1, 7, 14, 21].map((h) => ({ value: h, label: `${h}D` }));
const PAGE = 50;
const COLUMNS = [
  { key: "symbol", label: "Stock", sort: "symbol" },
  { key: "price", label: "Price", align: "right" },
  { key: "change", label: "Day", sort: "change", align: "right" },
  { key: "rank", label: "Outperformance", sort: "out_rank" },
  { key: "range", label: "80% range", sort: "range_width" },
  { key: "drawdown", label: "10%+ drop risk", sort: "drawdown", align: "right" },
  { key: "vol", label: "Vol (ann.)", sort: "volatility", align: "right" },
];

function useParamState(key, fallback, parse = (v) => v) {
  const [params, setParams] = useSearchParams();
  const raw = params.get(key);
  const value = raw === null ? fallback : parse(raw);
  const set = (v) => setParams((p) => {
    const next = new URLSearchParams(p);
    if (v === fallback || v === "" || v === null) next.delete(key);
    else next.set(key, String(v));
    next.delete("page");
    return next;
  }, { replace: true });
  return [value, set];
}

function exportCsv(rows, horizon) {
  const head = ["symbol", "company", "market", "sector", "price", "currency", "day_change", "outperform_rank", "outperform_prob", "q10", "q50", "q90", "drop_risk", "vol_annual", "regime", "as_of"];
  const lines = rows.map((r) => [r.symbol, r.company_name, r.exchange_mic, r.sector, r.last_close, r.currency, r.change_pct, r.out_rank, r.p_out, r.q10, r.q50, r.q90, r.drawdown_prob, r.vol_annual_forecast, r.vol_regime, r.as_of]
    .map((v) => (v === null || v === undefined ? "" : `"${String(v).replace(/"/g, '""')}"`)).join(","));
  const blob = new Blob([[head.join(","), ...lines].join("\n")], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `screener-${horizon}d.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}

function ScreenerPage() {
  const navigate = useNavigate();
  const [market, setMarket] = useParamState("market", "US");
  const [horizon, setHorizon] = useParamState("h", 21, Number);
  const [sort, setSort] = useParamState("sort", "out_rank");
  const [order, setOrder] = useParamState("order", "desc");
  const [sector, setSector] = useParamState("sector", "");
  const [regime, setRegime] = useParamState("regime", "");
  const [maxDd, setMaxDd] = useParamState("maxdd", "");
  const [minRank, setMinRank] = useParamState("minrank", "");
  const [params, setParams] = useSearchParams();
  const page = Math.max(0, Number(params.get("page") || 0));
  const [qInput, setQInput] = useState(params.get("q") ?? "");
  const [q, setQ] = useParamState("q", "");
  useEffect(() => {
    const t = setTimeout(() => { if (qInput !== q) setQ(qInput.trim()); }, 250);
    return () => clearTimeout(t);
  }, [qInput]); // eslint-disable-line react-hooks/exhaustive-deps

  const query = useQuery({
    queryKey: ["screener", market, horizon, sort, order, sector, regime, maxDd, minRank, q, page],
    queryFn: ({ signal }) => getScreener({
      market, horizon, sort, order, sector, regime, q,
      max_drawdown: maxDd ? Number(maxDd) : undefined,
      min_rank: minRank ? Number(minRank) : undefined,
      limit: PAGE, offset: page * PAGE,
    }, { signal }),
    placeholderData: keepPreviousData,
    staleTime: 60000,
    refetchInterval: (qq) => (qq.state.data?.pending ? 15000 : false),
  });
  const data = query.data;
  const rows = data?.rows ?? [];
  const scale = useMemo(() => Math.max(0.02, ...rows.flatMap((r) => [Math.abs(r.q10 ?? 0), Math.abs(r.q90 ?? 0)])), [rows]);
  const m = data?.measured ?? {};
  const pages = data ? Math.ceil(data.total / PAGE) : 0;

  function onSort(col) {
    if (!col.sort) return;
    if (sort === col.sort) setOrder(order === "desc" ? "asc" : "desc");
    else {
      setSort(col.sort);
      setOrder(col.sort === "symbol" || col.sort === "drawdown" || col.sort === "volatility" ? "asc" : "desc");
    }
  }

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="type-page">Screener</h1>
          <p className="mt-1 max-w-2xl text-sm text-term-muted">
            Every stock scored after each close. Sort by the outperformance rank (a small measured edge across many stocks), or screen on range and drop risk.
          </p>
        </div>
        <div className="flex items-center gap-2 text-2xs text-term-muted">
          {data?.as_of ? <span>Scores as of {data.as_of}</span> : null}
          {data?.model_version ? <Badge>{data.model_version}</Badge> : null}
        </div>
      </header>

      <Card pad={false}>
        <div className="flex flex-wrap items-center gap-3 border-b border-term-border p-3">
          <Segmented options={MARKETS} value={market} onChange={setMarket} ariaLabel="Market" />
          <Segmented options={HORIZONS} value={horizon} onChange={setHorizon} ariaLabel="Horizon" />
          <label className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-term-faint" aria-hidden="true" />
            <input className="term-input w-48 py-1.5 pl-8 text-xs" placeholder="Filter by symbol or name" value={qInput} onChange={(e) => setQInput(e.target.value)} aria-label="Filter by symbol or name" />
          </label>
          <select className="term-input py-1.5 text-xs" value={sector} onChange={(e) => setSector(e.target.value)} aria-label="Sector">
            <option value="">All sectors</option>
            {(data?.sectors ?? []).map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <select className="term-input py-1.5 text-xs" value={regime} onChange={(e) => setRegime(e.target.value)} aria-label="Volatility regime">
            <option value="">Any volatility</option>
            <option value="low">Calm (low)</option>
            <option value="normal">Normal</option>
            <option value="high">Elevated (high)</option>
          </select>
          <select className="term-input py-1.5 text-xs" value={maxDd} onChange={(e) => setMaxDd(e.target.value)} aria-label="Maximum drop risk">
            <option value="">Any drop risk</option>
            <option value="0.05">Drop risk ≤ 5%</option>
            <option value="0.1">Drop risk ≤ 10%</option>
            <option value="0.2">Drop risk ≤ 20%</option>
          </select>
          <select className="term-input py-1.5 text-xs" value={minRank} onChange={(e) => setMinRank(e.target.value)} aria-label="Minimum rank">
            <option value="">Any rank</option>
            <option value="0.9">Top 10%</option>
            <option value="0.75">Top 25%</option>
            <option value="0.5">Top half</option>
          </select>
          <button type="button" className="term-btn-ghost ml-auto py-1.5 text-xs" disabled={!rows.length} onClick={() => exportCsv(rows, horizon)}>
            <Download className="h-3.5 w-3.5" aria-hidden="true" /> CSV
          </button>
        </div>

        {m.out_ic !== undefined && m.out_ic !== null ? (
          <p className="border-b border-term-border px-3 py-2 text-xs text-term-muted">
            Measured at {horizon} days: the ranking&apos;s correlation with later returns was <b className="text-term-text">{fmtNum(m.out_ic, 3)}</b> (t = {m.out_ic_t}); top-decile stocks beat bottom-decile ones by <b className="text-term-text">{fmtPct(m.out_decile_spread, 2)}</b> per period on average. Ranges contained <b className="text-term-text">{fmtPct(m.range_coverage_80, 1)}</b> of outcomes (target 80%). <Link to="/model" className="term-link">Details</Link>
          </p>
        ) : null}

        {query.isError ? (
          <div className="p-4"><ErrorState title="Screener unavailable" detail={extractBackendDetail(query.error, "screener unreachable")} onRetry={() => void query.refetch()} /></div>
        ) : data?.pending && !rows.length ? (
          <p className="p-6 text-center text-sm text-term-muted">Scoring every stock for the first time. This takes a couple of minutes; the table fills in automatically.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="term-table min-w-[860px]">
              <thead>
                <tr>
                  {COLUMNS.map((c) => (
                    <th key={c.key} className={c.align === "right" ? "text-right" : ""} aria-sort={sort === c.sort ? (order === "desc" ? "descending" : "ascending") : undefined}>
                      {c.sort ? (
                        <button type="button" className={`inline-flex items-center gap-1 uppercase tracking-[0.06em] hover:text-term-text ${sort === c.sort ? "text-term-text" : ""}`} onClick={() => onSort(c)}>
                          {c.label}
                          {sort === c.sort ? (order === "desc" ? <ArrowDown className="h-3 w-3" /> : <ArrowUp className="h-3 w-3" />) : null}
                        </button>
                      ) : c.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className={query.isFetching ? "opacity-70" : ""}>
                {query.isLoading ? Array.from({ length: 10 }).map((_, i) => (
                  <tr key={i}><td colSpan={COLUMNS.length}><div className="h-6 animate-pulse rounded bg-term-panel2" /></td></tr>
                )) : rows.map((r) => (
                  <tr
                    key={r.symbol}
                    className="cursor-pointer"
                    tabIndex={0}
                    onClick={() => navigate(`/security/${encodeURIComponent(r.symbol)}`)}
                    onKeyDown={(e) => { if (e.key === "Enter") navigate(`/security/${encodeURIComponent(r.symbol)}`); }}
                  >
                    <td className="max-w-[16rem]">
                      <div className="font-semibold text-term-text">{r.symbol}</div>
                      <div className="truncate text-2xs text-term-muted" title={r.company_name}>{r.company_name}{r.sector ? ` · ${r.sector}` : ""}</div>
                    </td>
                    <td className="term-num text-right text-term-text">{fmtMoney(r.last_close, r.currency || "USD")}</td>
                    <td className={`term-num text-right ${r.change_pct > 0 ? "text-term-green" : r.change_pct < 0 ? "text-term-red" : "text-term-muted"}`}>{fmtPct(r.change_pct, 2, { signed: true })}</td>
                    <td className="w-44">
                      {r.out_rank !== null && r.out_rank !== undefined ? (
                        <div>
                          <div className="flex justify-between text-2xs"><span className="text-term-text">{fmtRank(r.out_rank)}</span><span className="term-num text-term-muted">{fmtPct(r.p_out, 1)}</span></div>
                          <RankMeter value={r.out_rank} className="mt-1" />
                        </div>
                      ) : <span className="text-2xs text-term-faint">US only</span>}
                    </td>
                    <td className="w-56">
                      <RangeBar low={r.q10} mid={r.q50} high={r.q90} scale={scale} showLabels />
                    </td>
                    <td className="term-num text-right text-term-text">{fmtPct(r.drawdown_prob, 0)}</td>
                    <td className="term-num text-right text-term-text">
                      {fmtPct(r.vol_annual_forecast, 0)}
                      {r.vol_regime === "high" ? <span className="ml-1 text-2xs text-term-amber" title="Volatility above its 1-year norm">▲</span> : r.vol_regime === "low" ? <span className="ml-1 text-2xs text-term-muted" title="Volatility below its 1-year norm">▼</span> : null}
                    </td>
                  </tr>
                ))}
                {!query.isLoading && rows.length === 0 && !data?.pending ? (
                  <tr><td colSpan={COLUMNS.length} className="py-8 text-center text-sm text-term-muted">No stocks match these filters.</td></tr>
                ) : null}
              </tbody>
            </table>
          </div>
        )}
        {pages > 1 ? (
          <div className="flex items-center justify-between border-t border-term-border px-3 py-2 text-xs text-term-muted">
            <span>{data.total} stocks · page {page + 1} of {pages}</span>
            <div className="flex gap-2">
              <button type="button" className="term-btn-sm" disabled={page === 0} onClick={() => setParams((p) => { const n = new URLSearchParams(p); n.set("page", String(page - 1)); return n; })}>Previous</button>
              <button type="button" className="term-btn-sm" disabled={page + 1 >= pages} onClick={() => setParams((p) => { const n = new URLSearchParams(p); n.set("page", String(page + 1)); return n; })}>Next</button>
            </div>
          </div>
        ) : null}
      </Card>
      <p className="text-2xs text-term-faint">{data?.disclosure}</p>
    </div>
  );
}

export { ScreenerPage as default };
