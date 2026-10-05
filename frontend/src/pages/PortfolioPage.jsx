import React, { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Plus, Trash2, Upload } from "lucide-react";
import { extractBackendDetail } from "../api/client";
import { postPortfolioRisk } from "../api/risk";
import usePortfolio from "../hooks/usePortfolio";
import useWatchlist from "../hooks/useWatchlist";
import Card from "../components/ui/Card";
import Segmented from "../components/ui/Segmented";
import Stat from "../components/ui/Stat";
import { BarList, Heatmap } from "../components/charts/Bars";
import { fmtNum, fmtPct } from "../utils/format";

const LOOKBACKS = [
  { value: 63, label: "3M" },
  { value: 126, label: "6M" },
  { value: 252, label: "1Y" },
  { value: 504, label: "2Y" },
];

function HoldingsEditor({ holdings, setHoldings }) {
  const [sym, setSym] = useState("");
  const [amt, setAmt] = useState("");
  const total = holdings.reduce((s, h) => s + Math.abs(h.amount), 0);
  function add(e) {
    e.preventDefault();
    const s = sym.trim().toUpperCase();
    const a = Number(amt);
    if (!s || !(a > 0)) return;
    setHoldings((list) => [...list.filter((h) => h.symbol !== s), { symbol: s, amount: a }]);
    setSym("");
    setAmt("");
  }
  return (
    <div>
      <form className="flex flex-wrap gap-2" onSubmit={add}>
        <input className="term-input w-32 uppercase" placeholder="Symbol" value={sym} onChange={(e) => setSym(e.target.value)} aria-label="Symbol" spellCheck={false} />
        <input className="term-input term-num w-36" placeholder="Amount" inputMode="decimal" value={amt} onChange={(e) => setAmt(e.target.value.replace(/[^\d.]/g, ""))} aria-label="Amount invested" />
        <button type="submit" className="term-btn"><Plus className="h-4 w-4" aria-hidden="true" />Add</button>
      </form>
      {holdings.length ? (
        <table className="term-table mt-3">
          <thead><tr><th>Holding</th><th className="text-right">Amount</th><th className="text-right">Weight</th><th /></tr></thead>
          <tbody>
            {holdings.map((h) => (
              <tr key={h.symbol}>
                <td><Link to={`/security/${encodeURIComponent(h.symbol)}`} className="font-semibold text-term-text hover:text-term-accent">{h.symbol}</Link></td>
                <td className="text-right">
                  <input
                    className="term-input term-num w-28 py-1 text-right text-xs"
                    inputMode="decimal"
                    value={String(h.amount)}
                    onChange={(e) => {
                      const v = Number(e.target.value.replace(/[^\d.]/g, ""));
                      setHoldings((list) => list.map((x) => (x.symbol === h.symbol ? { ...x, amount: Number.isFinite(v) ? v : 0 } : x)));
                    }}
                    aria-label={`${h.symbol} amount`}
                  />
                </td>
                <td className="term-num text-right text-term-muted">{total ? fmtPct(h.amount / total, 1) : "—"}</td>
                <td className="text-right">
                  <button type="button" className="term-icon-btn" aria-label={`Remove ${h.symbol}`} onClick={() => setHoldings((list) => list.filter((x) => x.symbol !== h.symbol))}>
                    <Trash2 className="h-4 w-4" />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="mt-3 text-sm text-term-muted">Add holdings with the amount you hold in each (any currency, used only for weights).</p>
      )}
    </div>
  );
}

function PortfolioPage() {
  const { holdings, setHoldings } = usePortfolio();
  const { symbols: watch } = useWatchlist();
  const [lookback, setLookback] = useState(252);
  const m = useMutation({ mutationFn: () => postPortfolioRisk(holdings.map((h) => ({ symbol: h.symbol, weight: h.amount })), lookback) });
  const r = m.data;
  const contributions = (r?.holdings ?? []).map((h) => ({ label: h.symbol, value: h.risk_contribution ?? 0 })).sort((a, b) => b.value - a.value);
  const weights = (r?.holdings ?? []).map((h) => ({ label: h.symbol, value: h.weight ?? 0, color: "var(--series-3)" })).sort((a, b) => b.value - a.value);
  return (
    <div className="space-y-4">
      <header>
        <h1 className="type-page">Portfolio risk</h1>
        <p className="mt-1 max-w-2xl text-sm text-term-muted">How much your holdings move together, what a bad day could cost, and which positions carry the risk. Saved in this browser for your account.</p>
      </header>

      <div className="grid gap-4 xl:grid-cols-[26rem_minmax(0,1fr)]">
        <Card
          title="Holdings"
          actions={watch.length ? (
            <button type="button" className="term-btn-sm" onClick={() => setHoldings((list) => [...list, ...watch.filter((s) => !list.some((h) => h.symbol === s)).map((s) => ({ symbol: s, amount: 1000 }))])}>
              <Upload className="h-3 w-3" aria-hidden="true" />From watchlist
            </button>
          ) : null}
        >
          <HoldingsEditor holdings={holdings} setHoldings={setHoldings} />
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <Segmented options={LOOKBACKS} value={lookback} onChange={setLookback} ariaLabel="History window" />
            <button type="button" className="term-btn" disabled={holdings.length === 0 || m.isPending} onClick={() => m.mutate()}>
              {m.isPending ? "Analyzing…" : "Analyze risk"}
            </button>
          </div>
          {m.isError ? <p className="mt-3 text-sm text-term-amber">{extractBackendDetail(m.error, "analysis failed")}</p> : null}
        </Card>

        {r ? (
          <div className="space-y-4">
            <Card title="Summary" subtitle={`${r.start} – ${r.end} · ${r.observations} sessions${r.benchmark ? ` · beta vs ${r.benchmark}` : ""}`}>
              <div className="grid grid-cols-2 gap-x-4 gap-y-4 sm:grid-cols-3 lg:grid-cols-6">
                <Stat label="Volatility" value={fmtPct(r.vol_annual, 1)} note="annualized" />
                <Stat label="1-day VaR 95%" value={fmtPct(r.var_95_1d, 2)} note="1 day in 20" />
                <Stat label="1-day CVaR 95%" value={fmtPct(r.cvar_95_1d, 2)} note="avg of those days" tone="negative" />
                <Stat label="Max drawdown" value={fmtPct(r.max_drawdown, 1)} />
                <Stat label="Beta" value={fmtNum(r.beta, 2)} note={r.market_correlation != null ? `corr ${fmtNum(r.market_correlation, 2)}` : undefined} />
                <Stat label="Diversification" value={`${fmtNum(r.diversification_ratio, 2)}×`} note="1× = none" />
              </div>
              {Object.keys(r.unavailable ?? {}).length ? (
                <p className="mt-3 text-xs text-term-amber">Left out (no usable history): {Object.keys(r.unavailable).join(", ")}</p>
              ) : null}
            </Card>
            <div className="grid gap-4 lg:grid-cols-2">
              <Card title="Share of risk" subtitle="Each holding's contribution to portfolio volatility (sums to 100%)">
                <BarList data={contributions} />
              </Card>
              <Card title="Share of capital" subtitle="Compare with share of risk: a big gap means a position is riskier than its size">
                <BarList data={weights} />
              </Card>
            </div>
            <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_18rem]">
              <Card title="Correlations" subtitle="Daily returns over the window. Lower means better diversification.">
                <Heatmap labels={r.correlation?.symbols ?? []} matrix={r.correlation?.matrix ?? []} />
              </Card>
              <Card title="Worst days" subtitle="Portfolio return">
                <ul className="divide-y divide-term-border/60 text-sm">
                  {(r.worst_days ?? []).map((d) => (
                    <li key={d.date} className="flex justify-between py-1.5">
                      <span className="term-num text-term-muted">{d.date}</span>
                      <span className="term-num text-term-red">{fmtPct(d.return, 2, { signed: true })}</span>
                    </li>
                  ))}
                </ul>
              </Card>
            </div>
          </div>
        ) : (
          <Card>
            <div className="flex min-h-[16rem] flex-col items-center justify-center text-center">
              <p className="text-sm text-term-text">Add two or more holdings and press <b>Analyze risk</b>.</p>
              <p className="mt-1 max-w-sm text-xs text-term-muted">Uses stored daily closes. Volatility, VaR and correlations are historical; they describe how the mix behaved, not a guarantee of how it will.</p>
            </div>
          </Card>
        )}
      </div>
    </div>
  );
}

export { PortfolioPage as default };
