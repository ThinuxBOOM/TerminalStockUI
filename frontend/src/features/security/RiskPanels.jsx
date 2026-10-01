import React, { useMemo, useState } from "react";
import Card from "../../components/ui/Card";
import Segmented from "../../components/ui/Segmented";
import Stat from "../../components/ui/Stat";
import { fmtCompact, fmtMoney, fmtNum, fmtPct, isNum } from "../../utils/format";

function RiskSummaryCard({ risk, loading, onOpen }) {
  return (
    <Card title="Risk" subtitle={risk ? `${risk.start} – ${risk.end}${risk.benchmark ? ` · vs ${risk.benchmark}` : ""}` : "Historical, from daily closes"}>
      {loading ? <div className="h-28 animate-pulse rounded-md bg-term-panel2" /> : null}
      {!loading && risk ? (
        <>
          <div className="grid grid-cols-2 gap-x-4 gap-y-3">
            <Stat size="sm" label="Volatility (1y)" value={fmtPct(risk.vol?.d252, 1)} note="annualized" />
            <Stat size="sm" label="Beta" value={fmtNum(risk.market?.beta, 2)} note={isNum(risk.market?.correlation) ? `corr ${fmtNum(risk.market.correlation, 2)}` : undefined} />
            <Stat size="sm" label="Max drawdown (1y)" value={fmtPct(risk.drawdown?.max, 1)} tone="negative" />
            <Stat size="sm" label="1-day VaR (95%)" value={fmtPct(risk.var_95?.d1?.var, 1)} note="loss on 1 day in 20" />
          </div>
          <button type="button" className="term-link mt-4 text-sm" onClick={onOpen}>Full risk profile →</button>
        </>
      ) : null}
      {!loading && !risk ? <p className="text-sm text-term-muted">Risk metrics unavailable.</p> : null}
    </Card>
  );
}

function PositionSizer({ price, currency, forecasts }) {
  const [account, setAccount] = useState("10000");
  const [riskPct, setRiskPct] = useState("1");
  const [horizon, setHorizon] = useState(7);
  const f = forecasts?.[horizon];
  const stop = f ? Math.abs(Math.min(0, f.quantiles?.["0.10"] ?? NaN)) : NaN;
  const result = useMemo(() => {
    const a = Number(account);
    const r = Number(riskPct) / 100;
    if (!(a > 0) || !(r > 0) || !(stop > 0) || !(price > 0)) return null;
    const value = Math.min(a, (a * r) / stop);
    return { value, shares: Math.floor(value / price), stopPrice: price * (1 - stop), riskAmount: a * r };
  }, [account, riskPct, stop, price]);
  return (
    <Card
      title="Position size"
      subtitle="Size so that a move to the model's 10th-percentile outcome loses only your chosen risk"
      actions={<Segmented options={[1, 7, 14, 21].map((h) => ({ value: h, label: `${h}D` }))} value={horizon} onChange={setHorizon} ariaLabel="Holding period" size="xs" />}
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-xs text-term-muted">
          Account value ({currency || "USD"})
          <input className="term-input term-num mt-1 w-full" inputMode="decimal" value={account} onChange={(e) => setAccount(e.target.value.replace(/[^\d.]/g, ""))} />
        </label>
        <label className="text-xs text-term-muted">
          Risk per position (%)
          <input className="term-input term-num mt-1 w-full" inputMode="decimal" value={riskPct} onChange={(e) => setRiskPct(e.target.value.replace(/[^\d.]/g, ""))} />
        </label>
      </div>
      {result ? (
        <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-4">
          <Stat size="sm" label="Position" value={fmtMoney(result.value, currency, 0)} note={`${fmtPct(result.value / Number(account), 0)} of account`} />
          <Stat size="sm" label="Shares" value={result.shares.toLocaleString()} note={`at ${fmtMoney(price, currency)}`} />
          <Stat size="sm" label="Stop level" value={fmtMoney(result.stopPrice, currency)} note={`−${fmtPct(stop, 1)} (${horizon}d 10th pct)`} />
          <Stat size="sm" label="Money at risk" value={fmtMoney(result.riskAmount, currency, 0)} />
        </dl>
      ) : (
        <p className="mt-3 text-sm text-term-muted">Enter an account value and a risk percentage.</p>
      )}
      <p className="mt-3 text-2xs text-term-faint">One in ten {horizon}-day periods historically ended below the stop. Gaps can overshoot any stop.</p>
    </Card>
  );
}

function RiskTab({ risk, loading, error, onRetry, price, currency, forecasts }) {
  if (loading) return <div className="h-80 animate-pulse rounded-lg bg-term-panel" />;
  if (error || !risk) {
    return (
      <Card title="Risk metrics unavailable">
        <p className="text-sm text-term-muted">{error || "No risk data."}</p>
        {onRetry ? <button type="button" className="term-btn-ghost mt-3 text-sm" onClick={onRetry}>Retry</button> : null}
      </Card>
    );
  }
  const f21 = forecasts?.[21];
  const vars = [
    ["1 day", risk.var_95?.d1],
    ["1 week", risk.var_95?.d5],
    ["1 month", risk.var_95?.d21],
  ];
  return (
    <div className="space-y-4">
      <div className="grid gap-4 md:grid-cols-2 2xl:grid-cols-3">
        <Card title="Volatility" subtitle="Annualized standard deviation of daily returns">
          <dl className="grid grid-cols-2 gap-x-4 gap-y-3">
            <Stat size="sm" label="Last month" value={fmtPct(risk.vol?.d21, 1)} />
            <Stat size="sm" label="Last 3 months" value={fmtPct(risk.vol?.d63, 1)} />
            <Stat size="sm" label="Last year" value={fmtPct(risk.vol?.d252, 1)} />
            <Stat size="sm" label="Forecast (21d)" value={fmtPct(f21?.volatility_forecast_annual, 1)} note={f21 ? `regime: ${f21.volatility_regime}` : undefined} />
          </dl>
        </Card>
        <Card title="Drawdowns" subtitle="Fall from the running peak">
          <dl className="grid grid-cols-2 gap-x-4 gap-y-3">
            <Stat size="sm" label="Current" value={fmtPct(risk.drawdown?.current, 1)} tone={risk.drawdown?.current < -0.1 ? "negative" : "neutral"} />
            <Stat size="sm" label="Worst, last year" value={fmtPct(risk.drawdown?.max, 1)} note={risk.drawdown?.max_start ? `${risk.drawdown.max_start} → ${risk.drawdown.max_end}` : undefined} />
            <Stat size="sm" label="Worst, full history" value={fmtPct(risk.drawdown_full?.max, 1)} note={`since ${risk.start}`} />
            <Stat size="sm" label="Worst / best day" value={`${fmtPct(risk.worst_day, 1)} / ${fmtPct(risk.best_day, 1, { signed: true })}`} />
          </dl>
        </Card>
        <Card title="Market sensitivity" subtitle={risk.benchmark ? `Against ${risk.benchmark}, last year` : "Benchmark unavailable"}>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-3">
            <Stat size="sm" label="Beta" value={fmtNum(risk.market?.beta, 2)} note="move per 1% market move" />
            <Stat size="sm" label="Correlation" value={fmtNum(risk.market?.correlation, 2)} />
            <Stat size="sm" label="Sharpe (1y)" value={fmtNum(risk.performance?.sharpe, 2)} />
            <Stat size="sm" label="Sortino (1y)" value={fmtNum(risk.performance?.sortino, 2)} />
          </dl>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Value at risk" subtitle="Historical, 95%: the loss exceeded on 1 period in 20 (CVaR: the average loss when it is)">
          <table className="term-table">
            <thead>
              <tr><th>Holding period</th><th className="text-right">VaR 95%</th><th className="text-right">CVaR 95%</th></tr>
            </thead>
            <tbody>
              {vars.map(([label, v]) => (
                <tr key={label}>
                  <td className="text-term-text">{label}</td>
                  <td className="term-num text-right text-term-text">{fmtPct(v?.var, 1)}</td>
                  <td className="term-num text-right text-term-red">{fmtPct(v?.cvar, 1)}</td>
                </tr>
              ))}
              <tr>
                <td className="text-term-text">1 day, 99%</td>
                <td className="term-num text-right text-term-text">{fmtPct(risk.var_99?.d1?.var, 1)}</td>
                <td className="term-num text-right text-term-red">{fmtPct(risk.var_99?.d1?.cvar, 1)}</td>
              </tr>
            </tbody>
          </table>
        </Card>
        <Card title="Return and liquidity" subtitle="Last year">
          <dl className="grid grid-cols-2 gap-x-4 gap-y-3">
            <Stat size="sm" label="Return (ann.)" value={fmtPct(risk.performance?.return_ann, 1, { signed: true })} tone={risk.performance?.return_ann >= 0 ? "positive" : "negative"} />
            <Stat size="sm" label="Avg daily value traded" value={isNum(risk.liquidity?.avg_dollar_volume) ? `${fmtCompact(risk.liquidity.avg_dollar_volume)} ${currency || ""}` : "—"} note="last 3 months" />
            <Stat size="sm" label="Price impact (Amihud)" value={fmtNum(risk.liquidity?.amihud, 3)} note="|return| per 1B traded; higher = less liquid" />
            <Stat size="sm" label="History" value={`${risk.observations} sessions`} />
          </dl>
        </Card>
      </div>

      <PositionSizer price={price} currency={currency} forecasts={forecasts} />
    </div>
  );
}

export { PositionSizer, RiskSummaryCard, RiskTab };
