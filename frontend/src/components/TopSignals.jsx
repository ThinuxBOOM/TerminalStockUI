import React from "react";
import { Link } from "react-router-dom";
import Skeleton from "./Skeleton";
import ErrorState from "./ErrorState";
import EmptyState from "./EmptyState";
import CurrencyValue from "./CurrencyValue";

const MIC_LABELS = {
  XNYS: "New York (NYSE)",
  XNAS: "Nasdaq",
  XSHG: "Shanghai (SSE)",
  XPAR: "Paris (Euronext)",
  XAMS: "Amsterdam (Euronext)",
  XBRU: "Brussels (Euronext)",
};

function pct0(p) {
  if (typeof p !== "number" || !Number.isFinite(p)) return "—";
  return `${(p * 100).toFixed(0)}%`;
}

function directionOf(row, tone) {
  // Consistent visual language: Rising / Falling / Neutral (never guarantee).
  // Buy tone leans Rising, short tone leans Falling; neutral when prob ~50%.
  const p = row.signal_probability;
  if (typeof p === "number" && Number.isFinite(p)) {
    if (p >= 0.55) return { word: "RISING", arrow: "↑", cls: "text-term-green" };
    if (p <= 0.45) return { word: "FALLING", arrow: "↓", cls: "text-term-red" };
  }
  if (tone === "buy") return { word: "RISING", arrow: "↑", cls: "text-term-green" };
  if (tone === "short") return { word: "FALLING", arrow: "↓", cls: "text-term-red" };
  return { word: "NEUTRAL", arrow: "→", cls: "text-term-muted" };
}

function SignalRow({ row, tone, horizon }) {
  const prob = row.signal_probability;
  const bar = Math.round(Math.max(0, Math.min(1, Number(prob) || 0)) * 100);
  const dir = directionOf(row, tone);
  const barCls = tone === "buy" ? "bg-term-green" : "bg-term-red";
  return (
    <li className="group flex items-center justify-between gap-2 border-b border-term-border py-2 transition-colors last:border-0 hover:bg-term-panel2/60 focus-within:bg-term-panel2/60">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-x-2">
          <Link
            to={`/security/${encodeURIComponent(row.symbol)}`}
            className="font-bold text-term-green hover:underline focus-visible:underline"
          >
            {row.symbol}
          </Link>
          {/* AAPL 64%↑ 21D style: prob + arrow + horizon in one scan */}
          <span className="tnum term-num text-sm font-bold text-term-text" aria-label={`${row.symbol} ${pct0(prob)} ${dir.word} ${horizon} day forecast`}>
            {pct0(prob)}{dir.arrow}
          </span>
          <span className={`text-[11px] font-bold tracking-wide ${dir.cls}`}>
            {dir.word}
          </span>
          <span className="rounded border border-term-border px-1 py-px text-[10px] font-semibold text-term-muted" title={`Forecast horizon ${horizon} trading days`}>
            {horizon}D
          </span>
          <span className="hidden text-[11px] text-term-muted group-hover:inline" aria-hidden="true">
            {row.verdict}
          </span>
        </div>
        <div className="mt-0.5 truncate text-[11px] text-term-muted" title={row.company_name}>
          {row.company_name} · <CurrencyValue value={row.price} currency={row.currency} />
        </div>
        <div className="mt-1 h-1.5 w-full overflow-hidden rounded bg-term-panel2" role="img" aria-label={`${row.symbol} signal ${pct0(prob)} ${dir.word}`}>
          <div className={`h-full rounded ${barCls}`} style={{ width: `${bar}%` }} />
        </div>
        <div className="mt-0.5 line-clamp-1 text-[11px] text-term-muted">{row.what_it_means}</div>
      </div>
      <Link
        to={`/security/${encodeURIComponent(row.symbol)}`}
        className="term-btn-sm shrink-0 opacity-100 focus-visible:opacity-100 md:opacity-0 md:group-hover:opacity-100 md:group-focus-within:opacity-100"
        aria-label={`Open ${row.symbol} security brief`}
      >
        OPEN →
      </Link>
    </li>
  );
}

function TopSignals({ data, isLoading, isError, error, horizon, onHorizon, onRetry }) {
  const horizons = [1, 7, 14, 21];
  const entries = Object.entries(data?.markets ?? {});
  return (
    <section className="term-panel-hero min-w-0 p-4" aria-labelledby="home-signals">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <h2 id="home-signals" className="text-base font-extrabold text-term-text">
            Forecast signals
          </h2>
          <p className="mt-0.5 text-xs text-term-muted">
            What to research further vs be careful with — {horizon}D outlook, math + news. Not investment advice.
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
        <div className="mt-3"><Skeleton label="finding top ideas…" lines={5} variant="table" /></div>
      )}
      {isError && (
        <div className="mt-3">
          <ErrorState
            title="Forecast signals unavailable"
            detail={error instanceof Error ? error.message : "Signals endpoint unreachable."}
            onRetry={onRetry}
          />
        </div>
      )}
      {!isLoading && !isError && data && entries.length === 0 && (
        <div className="mt-3">
          <EmptyState title="No signals yet" detail="Signal scan returned no markets — retry or check data health." actionLabel="Retry" onAction={onRetry} />
        </div>
      )}
      {!isLoading && !isError && data && entries.length > 0 && (
        <div className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {entries.map(([mic, bucket]) => (
            <div key={mic} className="term-panel-nested min-w-0 p-3">
              <h3 className="text-xs font-bold uppercase tracking-widest text-term-muted">
                {MIC_LABELS[mic] ?? mic}
              </h3>
              <p className="mt-2 text-[11px] font-bold text-term-green">▲ RESEARCH FURTHER ({bucket.top_buy.length})</p>
              {bucket.top_buy.length === 0 ? (
                <p className="mt-1 text-[11px] text-term-muted" role="status">No ideas — data still warming up.</p>
              ) : (
                <ul className="mt-1" aria-label={`${mic} research further`}>
                  {bucket.top_buy.map((r) => <SignalRow key={`b-${r.symbol}`} row={r} tone="buy" horizon={horizon} />)}
                </ul>
              )}
              <p className="mt-3 text-[11px] font-bold text-term-red">▼ BE CAREFUL ({bucket.top_short.length})</p>
              {bucket.top_short.length === 0 ? (
                <p className="mt-1 text-[11px] text-term-muted" role="status">Nothing flagged.</p>
              ) : (
                <ul className="mt-1" aria-label={`${mic} be careful`}>
                  {bucket.top_short.map((r) => <SignalRow key={`s-${r.symbol}`} row={r} tone="short" horizon={horizon} />)}
                </ul>
              )}
            </div>
          ))}
        </div>
      )}
      {!isLoading && !isError && (
        <p className="mt-2 text-[11px] text-term-muted">
          How it works: 80% math forecast + 20% news mood. {data?.formula ?? ""} Signals are research starting points, never guarantees.
        </p>
      )}
    </section>
  );
}

export { TopSignals as default };
