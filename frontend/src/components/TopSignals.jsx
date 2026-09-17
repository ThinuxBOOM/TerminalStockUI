import React from "react";
import { Link } from "react-router-dom";
import Skeleton from "./Skeleton";
import ErrorState from "./ErrorState";
import CurrencyValue from "./CurrencyValue";

const MIC_LABELS = {
  XNYS: "New York (NYSE)",
  XNAS: "Nasdaq",
  XSHG: "Shanghai (SSE)",
  XPAR: "Paris (Euronext)",
  XAMS: "Amsterdam (Euronext)",
  XBRU: "Brussels (Euronext)",
};

function pct1(p) {
  if (typeof p !== "number" || !Number.isFinite(p)) return "—";
  return `${(p * 100).toFixed(0)}%`;
}

function SignalRow({ row, tone }) {
  const prob = row.signal_probability;
  const bar = Math.round(Math.max(0, Math.min(1, prob)) * 100);
  return (
    <li className="flex items-center justify-between gap-2 border-b border-term-border py-2">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-x-2">
          <Link
            to={`/security/${encodeURIComponent(row.symbol)}`}
            className="font-bold text-term-green hover:underline"
          >
            {row.symbol}
          </Link>
          <span className="term-num text-sm font-bold text-term-text">{pct1(prob)}</span>
          <span className={`text-[11px] font-semibold ${tone === "buy" ? "text-term-green" : "text-term-red"}`}>
            {row.verdict}
          </span>
        </div>
        <div className="mt-0.5 truncate text-[11px] text-term-muted" title={row.company_name}>
          {row.company_name} · <CurrencyValue value={row.price} currency={row.currency} />
        </div>
        <div className="mt-1 h-1.5 w-full overflow-hidden rounded bg-term-panel2" role="img" aria-label={`${row.symbol} signal ${pct1(prob)}`}>
          <div
            className={`h-full rounded ${tone === "buy" ? "bg-term-green" : "bg-term-red"}`}
            style={{ width: `${bar}%` }}
          />
        </div>
        <div className="mt-0.5 text-[11px] text-term-muted">{row.what_it_means}</div>
      </div>
      <Link to={`/security/${encodeURIComponent(row.symbol)}`} className="term-btn-sm shrink-0" aria-label={`Open ${row.symbol} details`}>
        DETAILS →
      </Link>
    </li>
  );
}

function TopSignals({ data, isLoading, isError, error, horizon, onHorizon, onRetry }) {
  const horizons = [1, 7, 14, 21];
  return (
    <section className="term-panel-hero min-w-0 p-4" aria-labelledby="home-signals">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <h2 id="home-signals" className="text-base font-extrabold text-term-text">
            What should I look at today?
          </h2>
          <p className="mt-0.5 text-xs text-term-muted">
            Top 5 ideas to research further + 5 to be careful with, per market. Math + fresh news combined — not orders, just a shortlist.
          </p>
        </div>
        <div className="flex items-center gap-1" role="group" aria-label="Signal horizon">
          {horizons.map((h) => (
            <button
              key={h}
              type="button"
              onClick={() => onHorizon(h)}
              aria-pressed={h === horizon}
              title={`Forecast ${h} trading day${h === 1 ? "" : "s"} ahead`}
              className={h === horizon ? "term-btn px-2.5 py-1 text-xs" : "term-btn-ghost px-2.5 py-1 text-xs"}
            >
              {h}D
            </button>
          ))}
        </div>
      </div>
      {isLoading && (
        <div className="mt-3"><Skeleton label="finding top ideas…" lines={5} /></div>
      )}
      {isError && (
        <div className="mt-3">
          <ErrorState
            title="Ideas unavailable"
            detail={error instanceof Error ? error.message : "Signals endpoint unreachable."}
            onRetry={onRetry}
          />
        </div>
      )}
      {!isLoading && !isError && data && (
        <div className="mt-3 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {Object.entries(data.markets ?? {}).map(([mic, bucket]) => (
            <div key={mic} className="term-panel-nested min-w-0 p-3">
              <h3 className="text-xs font-bold uppercase tracking-widest text-term-muted">
                {MIC_LABELS[mic] ?? mic}
              </h3>
              <p className="mt-1 text-[11px] font-bold text-term-green">▲ RESEARCH FURTHER ({bucket.top_buy.length})</p>
              {bucket.top_buy.length === 0 ? (
                <p className="mt-1 text-[11px] text-term-muted">No ideas — data still warming up. Try again soon.</p>
              ) : (
                <ul className="mt-1">
                  {bucket.top_buy.map((r) => <SignalRow key={`b-${r.symbol}`} row={r} tone="buy" />)}
                </ul>
              )}
              <p className="mt-2 text-[11px] font-bold text-term-red">▼ BE CAREFUL ({bucket.top_short.length})</p>
              {bucket.top_short.length === 0 ? (
                <p className="mt-1 text-[11px] text-term-muted">Nothing flagged.</p>
              ) : (
                <ul className="mt-1">
                  {bucket.top_short.map((r) => <SignalRow key={`s-${r.symbol}`} row={r} tone="short" />)}
                </ul>
              )}
            </div>
          ))}
        </div>
      )}
      {!isLoading && !isError && (
        <p className="mt-2 text-[11px] text-term-muted">
          How it works: 80% math forecast + 20% news mood. {data?.formula ?? ""} Not investment advice.
        </p>
      )}
    </section>
  );
}

export { TopSignals as default };
