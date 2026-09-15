import React, { memo, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getQuote } from "../api/client";
import {
  ALL_ASPI_MICS,
  ASPI_BENCHMARKS,
  ASPI_MICS,
  ASPI_TIMEFRAMES,
  benchmarkForMic,
  combineAspiProvenance,
  computeEqualWeightedIndex,
  getAspiSeries,
  getTop20Bars,
  getTop20Constituents,
  top20CacheKey,
  aspiCacheKey,
} from "../api/aspi";
import ProvenanceBadge from "./ProvenanceBadge";
import FreshnessBadge from "./FreshnessBadge";
import MarketStateBadge from "./MarketStateBadge";
import CurrencyValue from "./CurrencyValue";
import Skeleton from "./Skeleton";
import EmptyState from "./EmptyState";
import ErrorState, { StaleBanner } from "./ErrorState";

const GREEN = "#3ddc84";
const RED = "#ff5c5c";
const MUTED = "#5b6b85";
const GRID = "#1c2433";

const plainFmt = new Intl.NumberFormat("en", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const compactFmt = new Intl.NumberFormat("en", { maximumFractionDigits: 2 });

function formatPlain(v) {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  try {
    return plainFmt.format(v);
  } catch {
    return String(v);
  }
}

function signedPct(v) {
  if (v === null || v === undefined || !Number.isFinite(v)) {
    return { text: "unavailable", tone: "text-term-muted" };
  }
  const sign = v > 0 ? "+" : "";
  return {
    text: `${sign}${v.toFixed(2)}%`,
    tone: v > 0 ? "text-term-green" : v < 0 ? "text-term-red" : "text-term-muted",
  };
}

function errorMessage(err, fallback) {
  if (err instanceof Error && err.message) return err.message;
  const e = err;
  const data = e?.response?.data;
  if (typeof data === "string" && data) return data;
  if (data && typeof data === "object") {
    const detail = data.detail ?? data.message;
    if (typeof detail === "string" && detail) return detail;
  }
  if (typeof e?.message === "string" && e.message) return e.message;
  return fallback;
}

// Pure SVG line/area chart (same visual language as MarketGraphs.jsx).
// points: [{t, close}] for benchmarks or [{t, value}] for rebased composites.
function IndexLineSvg({ points, ariaSummary, valueLabel = "value" }) {
  const rows = useMemo(() => (Array.isArray(points) ? points : []), [points]);
  const W = 560;
  const H = 200;
  const padL = 52;
  const padR = 10;
  const padT = 10;
  const padB = 20;
  const vals = useMemo(
    () => rows.map((p) => Number(p.close ?? p.value)).filter((v) => Number.isFinite(v)),
    [rows]
  );
  if (rows.length < 2 || vals.length < 2) {
    return (
      <p className="rounded border border-term-border p-3 text-xs text-term-muted" role="status">
        Not enough points for a line (need 2+, have {rows.length}).
      </p>
    );
  }
  let min = Math.min(...vals);
  let max = Math.max(...vals);
  if (min === max) {
    min -= Math.abs(min) * 0.01 || 1;
    max += Math.abs(max) * 0.01 || 1;
  }
  const span = max - min || 1;
  const x = (i) => padL + (i / (rows.length - 1)) * (W - padL - padR);
  const y = (v) => padT + (1 - (v - min) / span) * (H - padT - padB);
  const d = rows
    .map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(Number(p.close ?? p.value)).toFixed(1)}`)
    .join(" ");
  const area = `${d} L${x(rows.length - 1).toFixed(1)},${H - padB} L${x(0).toFixed(1)},${H - padB} Z`;
  const up = vals[vals.length - 1] >= vals[0];
  const col = up ? GREEN : RED;
  const first = rows[0].t;
  const last = rows[rows.length - 1].t;
  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="w-full rounded border border-term-border bg-term-bg"
      role="img"
      aria-label={ariaSummary}
    >
      {[0.25, 0.5, 0.75].map((t) => (
        <line
          key={t}
          x1={padL}
          y1={padT + (H - padT - padB) * t}
          x2={W - padR}
          y2={padT + (H - padT - padB) * t}
          stroke={GRID}
          strokeWidth={1}
        />
      ))}
      <path d={area} fill={col} fillOpacity={0.12} />
      <path d={d} fill="none" stroke={col} strokeWidth={1.5} />
      <circle cx={x(rows.length - 1)} cy={y(vals[vals.length - 1])} r={2.5} fill={col}>
        <title>{`last ${valueLabel}: ${compactFmt.format(vals[vals.length - 1])} (${last})`}</title>
      </circle>
      <text x={padL - 5} y={padT + 9} fill={MUTED} fontSize={9} textAnchor="end">
        {compactFmt.format(max)}
      </text>
      <text x={padL - 5} y={H - padB} fill={MUTED} fontSize={9} textAnchor="end">
        {compactFmt.format(min)}
      </text>
      <text x={padL} y={H - 6} fill={MUTED} fontSize={9}>
        {first}
      </text>
      <text x={W - padR} y={H - 6} fill={MUTED} fontSize={9} textAnchor="end">
        {last}
      </text>
    </svg>
  );
}

// ---------------------------------------------------------------------------
// (a) Per-market ASPI/index chart: benchmark bars + timeframe selector +
// ProvenanceBadge + FreshnessBadge + MarketStateBadge, StaleBanner on
// fallback. No hardcoded prices — every number comes from the bars/quote
// payloads (or "unavailable" states when they don't).
function AspiChart({ mic, userId = null, tier = null, defaultTimeframe = "1d" }) {
  const cfg = benchmarkForMic(mic);
  const [tf, setTf] = useState(ASPI_TIMEFRAMES.includes(defaultTimeframe) ? defaultTimeframe : "1d");
  const series = useQuery({
    queryKey: aspiCacheKey(cfg?.mic ?? mic, tf, userId, tier),
    queryFn: ({ signal }) => getAspiSeries(cfg?.mic ?? mic, tf, { userId, tier, signal }),
    retry: false,
    staleTime: 120000,
  });
  const usedSymbol = series.data?.usedSymbol ?? null;
  // Quote for the RESOLVED symbol only: honest display currency + a real
  // (non-derived) market_state. Never synthesizes either when it fails.
  const quote = useQuery({
    queryKey: ["quote", usedSymbol ?? `aspi-pending:${cfg?.mic ?? mic}`],
    queryFn: ({ signal }) => getQuote(usedSymbol, undefined, { signal }),
    enabled: usedSymbol !== null,
    retry: false,
    staleTime: 30000,
  });
  const displayCurrency = quote.data?.currency ?? cfg?.currency ?? "USD";
  const currencyKnown = Boolean(quote.data?.currency);
  const badgeProvenance = quote.data?.provenance ?? series.data?.provenance ?? null;

  if (!cfg) {
    return (
      <EmptyState
        title={`No benchmark configured for ${String(mic ?? "").toUpperCase()}`}
        detail="Add the venue to ASPI_BENCHMARKS in src/api/aspi.js (symbols only — never prices)."
      />
    );
  }
  const d = series.data ?? null;
  return (
    <div className="min-w-0 rounded border border-term-border bg-term-bg p-3" aria-label={`${cfg.venue} index chart`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="min-w-0 truncate text-sm font-bold text-term-text">
          {cfg.venue} — {cfg.indexLabel}
        </h4>
        <span className="flex shrink-0 flex-wrap gap-1 text-[10px] text-term-muted">
          <span className="rounded border border-term-border px-1.5 py-0.5" title="resolved benchmark symbol">
            {d?.usedSymbol ?? cfg.indexSymbol}
          </span>
          {d?.isProxy && (
            <span className="rounded border border-term-amber px-1.5 py-0.5 text-term-amber" title={`proxy: canonical ${cfg.indexSymbol} needs backend ^-symbol support`}>
              PROXY
            </span>
          )}
        </span>
      </div>

      <div className="mt-2 flex gap-1" role="group" aria-label={`${cfg.mic} index timeframe`}>
        {ASPI_TIMEFRAMES.map((t) => (
          <button
            key={t}
            type="button"
            className={`rounded border px-2 py-0.5 text-[11px] ${t === tf ? "border-term-green text-term-green" : "border-term-border text-term-muted"}`}
            aria-pressed={t === tf}
            onClick={() => setTf(t)}
          >
            {t}
          </button>
        ))}
      </div>

      <div className="mt-2">
        {series.isLoading && <Skeleton label={`loading ${cfg.mic} index bars…`} lines={4} />}
        {series.isError && (
          <ErrorState
            title={`${cfg.mic} index unavailable`}
            detail={errorMessage(series.error, `GET /api/market_data/bars failed for ${cfg.indexSymbol}.`)}
            onRetry={() => void series.refetch()}
          />
        )}
        {!series.isLoading && !series.isError && (!d || d.points.length === 0) && (
          <EmptyState
            title={`${cfg.mic} index has no bars`}
            detail={`The bars endpoint returned no candles for ${cfg.indexSymbol}${(cfg.proxies ?? []).length > 0 ? ` or proxies ${(cfg.proxies ?? []).join(", ")}` : ""}. No placeholder is shown in place of market data.`}
            actionLabel="Retry"
            onAction={() => void series.refetch()}
          />
        )}
        {!series.isLoading && !series.isError && d && d.points.length > 0 && (
          <>
            <IndexLineSvg
              points={d.points}
              ariaSummary={`${cfg.indexLabel} (${d.usedSymbol}${d.isProxy ? ", proxy" : ""}), ${d.count} ${tf} bars from ${d.start} to ${d.end}, last close ${d.lastClose}`}
            />
            <div className="mt-1 flex flex-wrap items-baseline justify-between gap-2 text-xs">
              <span className="text-term-muted">
                Last close ({d.end}):{" "}
                <CurrencyValue value={d.lastClose} currency={displayCurrency} className="font-bold text-term-text" />
                {!currencyKnown && <span className="text-term-muted"> (venue ccy — quote currency unavailable)</span>}
              </span>
              <span className="text-[10px] text-term-muted">{d.count} bars</span>
            </div>
          </>
        )}
      </div>

      {badgeProvenance && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <MarketStateBadge state={quote.data?.market_state ?? null} provenance={badgeProvenance} />
          <FreshnessBadge p={series.data?.provenance ?? badgeProvenance} />
          <ProvenanceBadge p={series.data?.provenance ?? badgeProvenance} />
        </div>
      )}
      {d?.fallback_used && (
        <div className="mt-2">
          <StaleBanner detail={`${cfg.mic} index bars served with fallback_used=true`} />
        </div>
      )}
      <p className="mt-1 text-[10px] text-term-muted">
        Source: GET /api/markets/{cfg.mic}/index (fallback GET /api/market_data/bars) · symbol {d?.usedSymbol ?? cfg.indexSymbol}
        {d?.isProxy ? ` (proxy — canonical ${cfg.indexSymbol} needs backend ^-symbol support)` : ""} · {tf}.
        {d?.isProxy && displayCurrency !== cfg.currency
          ? ` Proxy is ${displayCurrency}-denominated; values in ${displayCurrency}, no conversion applied.`
          : ""}
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// (b) Top-20-only chart per market: top 20 constituents (turnover-sorted when
// the liquidity endpoint serves, screener-rank fallback otherwise) +
// equal-weighted rebased line + native-currency constituents table.
// No FX ranking anywhere: ordering is within one market only.
function Top20AspiChart({ mic, userId = null, tier = null }) {
  const cfg = benchmarkForMic(mic);
  const [showLine, setShowLine] = useState(false);
  const [weighting, setWeighting] = useState("equal");
  const constituents = useQuery({
    queryKey: top20CacheKey(cfg?.mic ?? mic, userId, tier),
    queryFn: ({ signal }) => getTop20Constituents(cfg?.mic ?? mic, { userId, tier, signal }),
    retry: false,
    staleTime: 120000,
  });
  const rows = useMemo(() => constituents.data?.rows ?? [], [constituents.data]);
  const symbols = useMemo(() => rows.map((r) => r.symbol).filter(Boolean), [rows]);
  // Cap map for cap-weighted mode: turnover is NOT market-cap — only a real
  // market_cap field opts in. Rows today carry no cap, so cap mode honestly
  // falls back to equal-weighted until the backend exposes market_cap.
  const capWeights = useMemo(() => {
    const out = {};
    let any = false;
    for (const r of rows) {
      const cap = Number(r?.market_cap);
      if (Number.isFinite(cap) && cap > 0 && r?.symbol) {
        out[String(r.symbol).toUpperCase()] = cap;
        any = true;
      }
    }
    return any ? out : null;
  }, [rows]);
  const bars = useQuery({
    queryKey: [...top20CacheKey(cfg?.mic ?? mic, userId, tier), "bars", "1d"],
    queryFn: ({ signal }) => getTop20Bars(symbols, "1d", { signal }),
    enabled: showLine && symbols.length > 0,
    retry: false,
    staleTime: 120000,
  });
  const composite = useMemo(
    () => (bars.data ? computeEqualWeightedIndex(bars.data.series, weighting === "cap" ? { weights: capWeights ?? {} } : {}) : null),
    [bars.data, weighting, capWeights]
  );
  const compositeProvenance = useMemo(() => {
    if (!bars.data) return null;
    const base = combineAspiProvenance(
      bars.data.series.map((s) => s.provenance).filter(Boolean),
      `aspi-top20-index:${cfg?.mic ?? mic}`
    );
    const isCap = composite?.weighting === "cap-weighted";
    const missing = new Set([...(base.missing_fields ?? []), ...(isCap ? [] : ["market-cap-weights"])]);
    return { ...base, source: `${base.source}+${isCap ? "cap-weighted" : "equal-weighted"}`, missing_fields: [...missing] };
  }, [bars.data, cfg?.mic, mic, composite?.weighting]);

  if (!cfg) {
    return (
      <EmptyState
        title={`No Top-20 universe for ${String(mic ?? "").toUpperCase()}`}
        detail="Add the venue to ASPI_BENCHMARKS in src/api/aspi.js."
      />
    );
  }
  return (
    <div className="min-w-0 rounded border border-term-border bg-term-bg p-3" aria-label={`${cfg.venue} Top-20 composite`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="min-w-0 truncate text-sm font-bold text-term-text">{cfg.venue} — Top-20 composite</h4>
        {constituents.data && (
          <span
            className="shrink-0 rounded border border-term-border px-1.5 py-0.5 text-[10px] text-term-muted"
            title={constituents.data.methodologyNote}
          >
            {constituents.data.methodology === "liquidity-turnover" ? "BY TURNOVER" : "SCREENER RANK (FALLBACK)"}
          </span>
        )}
      </div>
      {constituents.data?.methodologyNote && (
        <p className="mt-1 text-[10px] text-term-muted">{constituents.data.methodologyNote}</p>
      )}

      <div className="mt-2">
        {constituents.isLoading && <Skeleton label={`loading ${cfg.mic} top 20…`} lines={5} />}
        {constituents.isError && (
          <ErrorState
            title={`${cfg.mic} Top-20 unavailable`}
            detail={errorMessage(constituents.error, "Liquidity and screener endpoints both failed.")}
            onRetry={() => void constituents.refetch()}
          />
        )}
        {!constituents.isLoading && !constituents.isError && rows.length === 0 && (
          <EmptyState
            title={`No Top-20 constituents for ${cfg.mic}`}
            detail={constituents.data?.reason ?? "Upstream returned no rows."}
            actionLabel="Retry"
            onAction={() => void constituents.refetch()}
          />
        )}
        {!constituents.isLoading && !constituents.isError && rows.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[420px] text-xs">
              <caption className="sr-only">
                Top {rows.length} {cfg.venue} constituents in native currency — ordered within this market only, never ranked across currencies
              </caption>
              <thead>
                <tr className="border-b border-term-border text-left text-[10px] uppercase tracking-widest text-term-muted">
                  <th scope="col" className="py-1 pr-2">#</th>
                  <th scope="col" className="py-1 pr-2">Symbol</th>
                  <th scope="col" className="py-1 pr-2">Company</th>
                  <th scope="col" className="py-1 pr-2 text-right">Price (native)</th>
                  <th scope="col" className="py-1 text-right">Change</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => {
                  const chg = signedPct(r.change_pct);
                  const ccy = typeof r.currency === "string" && /^[A-Z]{3}$/.test(r.currency) ? r.currency : null;
                  return (
                    <tr key={`${r.symbol}-${i}`} className="border-b border-term-border">
                      <td className="py-1 pr-2 text-term-muted">{i + 1}</td>
                      <td className="py-1 pr-2 font-bold text-term-green">{r.symbol}</td>
                      <td className="max-w-[180px] truncate py-1 pr-2 text-term-muted" title={r.company_name ?? r.symbol}>
                        {r.company_name ?? "—"}
                      </td>
                      <td className="py-1 pr-2 text-right text-term-text">
                        {ccy ? (
                          <CurrencyValue value={r.price} currency={ccy} />
                        ) : (
                          <span title="currency unavailable — raw quote value, no code assumed">
                            {r.price === null || r.price === undefined ? "unavailable" : formatPlain(r.price)}
                          </span>
                        )}
                      </td>
                      <td className={`py-1 text-right ${chg.tone}`}>{chg.text}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {rows.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-1">
          <button
            type="button"
            className="term-btn-ghost text-xs"
            onClick={() => setShowLine((v) => !v)}
            aria-expanded={showLine}
            aria-label={`${showLine ? "Hide" : "Show"} ${cfg.mic} Top-20 ${weighting === "cap" ? "cap-weighted" : "equal-weighted"} line`}
          >
            {showLine ? "▾ HIDE TOP-20 LINE" : "▸ SHOW TOP-20 LINE"}
          </button>
          <span className="ml-1 flex gap-1" role="group" aria-label={`${cfg.mic} Top-20 weighting`}>
            {[["equal", "EQUAL"], ["cap", "CAP-WTD"]].map(([id, label]) => (
              <button
                key={id}
                type="button"
                onClick={() => setWeighting(id)}
                aria-pressed={weighting === id}
                title={id === "cap" ? "Cap-weighted when every used constituent carries a finite market_cap; otherwise falls back to equal-weighted" : "Equal-weighted mean of rebased closes (base 100)"}
                className={`rounded border px-2 py-0.5 text-[10px] ${weighting === id ? "border-term-green text-term-green" : "border-term-border text-term-muted"}`}
              >
                {label}
              </button>
            ))}
          </span>
          {!capWeights && weighting === "cap" && (
            <span className="text-[10px] text-term-amber" role="note" title="No constituent carries market_cap yet — composite falls back to equal-weighted">
              no market-cap weights — equal fallback
            </span>
          )}
        </div>
      )}
      {showLine && (
        <div className="mt-2">
          {bars.isLoading && <Skeleton label={`loading ${cfg.mic} top-20 bars…`} lines={4} />}
          {bars.isError && (
            <ErrorState
              title="Top-20 line unavailable"
              detail={errorMessage(bars.error, "Constituent bars failed.")}
              onRetry={() => void bars.refetch()}
            />
          )}
          {!bars.isLoading && !bars.isError && bars.data && (
            <>
              <p className="mb-1 text-[10px] text-term-muted" role="status">
                Based on {composite?.constituentsUsed ?? 0} of {composite?.constituentsRequested ?? rows.length} constituents
                {bars.data.skipped.length > 0
                  ? ` (skipped: ${bars.data.skipped.map((s) => s.symbol).join(", ")})`
                  : ""}
                {" "}· base 100{composite?.start ? ` · ${composite.start} → ${composite.end}` : ""}.
              </p>
              {composite && composite.points.length > 0 ? (
                <>
                  <p className="mb-1 text-[10px] text-term-muted" role="status">
                    {composite.weighting === "cap-weighted" ? "Cap-weighted" : "Equal-weighted"} · {composite.methodology}
                    {composite.reason ? ` · ${composite.reason}` : ""}
                  </p>
                  <IndexLineSvg
                    points={composite.points}
                    valueLabel="index points (base 100)"
                    ariaSummary={`${cfg.venue} Top-20 ${composite.weighting} line, base 100, ${composite.points.length} points from ${composite.start} to ${composite.end}, from ${composite.constituentsUsed} constituents`}
                  />
                </>
              ) : (
                <EmptyState
                  title="Top-20 line needs more bars"
                  detail={composite?.reason ?? "Not enough constituent history."}
                  actionLabel="Retry"
                  onAction={() => void bars.refetch()}
                />
              )}
              {compositeProvenance && (
                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                  <FreshnessBadge p={compositeProvenance} />
                  <ProvenanceBadge p={compositeProvenance} />
                </div>
              )}
            </>
          )}
        </div>
      )}

      {constituents.data?.provenance && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <FreshnessBadge p={constituents.data.provenance} />
          <ProvenanceBadge p={constituents.data.provenance} />
        </div>
      )}
      {constituents.data?.fallback_used && (
        <div className="mt-2">
          <StaleBanner detail={`${cfg.mic} Top-20 served with fallback_used=true`} />
        </div>
      )}
      <p className="mt-1 text-[10px] text-term-muted">
        Native currency only — constituents are ordered within {cfg.mic}, never ranked across currencies (FX gate).
        Line is {composite?.weighting ?? (weighting === "cap" ? "cap-weighted (fallback equal until market_cap lands)" : "equal-weighted")};
        index-weighted needs market-cap weights (see docs/API_CONTRACT.md M9 proposal).
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// HomePage section wiring all per-market charts. Collapsed by default so six
// markets × (index bars + liquidity + screener + up to 20 constituent bars)
// never fire on page load — each chart fetches only when expanded.
function MarketIndexCard({ mic, userId, tier, defaultOpen = false }) {
  const cfg = ASPI_BENCHMARKS[mic];
  const [showIndex, setShowIndex] = useState(defaultOpen);
  const [showTop20, setShowTop20] = useState(defaultOpen);
  if (!cfg) return null;
  return (
    <article className="min-w-0 rounded border border-term-border bg-term-panel p-3" aria-label={`${cfg.venue} market index`}>
      <div className="flex items-center justify-between gap-2">
        <h3 className="min-w-0 truncate text-sm font-bold text-term-text">
          {cfg.venue} <span className="font-normal text-term-muted">({mic})</span>
        </h3>
        <span className="shrink-0 text-[10px] text-term-muted">{cfg.indexLabel}</span>
      </div>
      <div className="mt-2 flex flex-wrap gap-1">
        <button
          type="button"
          className="term-btn-ghost text-xs"
          onClick={() => setShowIndex((v) => !v)}
          aria-expanded={showIndex}
          aria-label={`${showIndex ? "Hide" : "Show"} ${mic} ${cfg.indexLabel} chart`}
        >
          {showIndex ? "▾ HIDE INDEX" : "▸ INDEX CHART"}
        </button>
        <button
          type="button"
          className="term-btn-ghost text-xs"
          onClick={() => setShowTop20((v) => !v)}
          aria-expanded={showTop20}
          aria-label={`${showTop20 ? "Hide" : "Show"} ${mic} Top-20 chart`}
        >
          {showTop20 ? "▾ HIDE TOP-20" : "▸ TOP-20 CHART"}
        </button>
      </div>
      {showIndex && (
        <div className="mt-2">
          <MemoAspiChart mic={mic} userId={userId} tier={tier} />
        </div>
      )}
      {showTop20 && (
        <div className="mt-2">
          <MemoTop20AspiChart mic={mic} userId={userId} tier={tier} />
        </div>
      )}
    </article>
  );
}

// Disabled-venue card (CSE/XCOL): honest EmptyState, never data. Probe-ready:
// shows the vendor-symbol TODO + enable path, and the native endpoint detail
// when the backend 422s.
function DisabledMarketCard({ mic }) {
  const cfg = ASPI_BENCHMARKS[mic];
  if (!cfg) return null;
  return (
    <article className="min-w-0 rounded border border-dashed border-term-border bg-term-panel p-3" aria-label={`${cfg.venue} coming soon`}>
      <div className="flex items-center justify-between gap-2">
        <h3 className="min-w-0 truncate text-sm font-bold text-term-text">
          {cfg.venue} <span className="font-normal text-term-muted">({mic})</span>
        </h3>
        <span className="shrink-0 rounded border border-term-border px-1.5 py-0.5 text-[10px] text-term-muted">COMING SOON</span>
      </div>
      <div className="mt-2">
        <EmptyState
          title={`${cfg.indexLabel} — coming soon`}
          detail={`Vendor index symbol ${cfg.indexSymbol} is unverified and the XCOL venue is disabled in config/markets.yaml (enabled:false, ingest:false). No bars are fabricated. Native endpoint GET /api/markets/${mic}/index returns 422 until enabled.`}
        />
      </div>
      <p className="mt-2 text-[10px] text-term-muted">
        Enable: add the XCOL venue to <code>config/markets.yaml</code> + confirm the vendor index symbol (see
        docs/API_CONTRACT.md M9 proposal). Currency {cfg.currency} · {cfg.timezone} (probe-ready).
      </p>
    </article>
  );
}

const MemoMarketIndexCard = memo(MarketIndexCard);
const MemoDisabledMarketCard = memo(DisabledMarketCard);
const MemoAspiChart = memo(AspiChart);
const MemoTop20AspiChart = memo(Top20AspiChart);

function MarketIndicesSection({ userId = null, tier = null }) {
  const [all, setAll] = useState(false);
  const [tick, setTick] = useState(0);
  return (
    <section className="term-panel min-w-0 p-4" aria-labelledby="home-indices" id="market-indices">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="home-indices" className="term-label">
          Market indices &amp; Top-20 composites
        </h2>
        <span className="flex gap-1">
          <button
            type="button"
            className="term-btn-ghost text-xs"
            onClick={() => {
              setAll(true);
              setTick((t) => t + 1);
            }}
          >
            EXPAND ALL
          </button>
          <button
            type="button"
            className="term-btn-ghost text-xs"
            onClick={() => {
              setAll(false);
              setTick((t) => t + 1);
            }}
          >
            COLLAPSE ALL
          </button>
        </span>
      </div>
      <p className="mt-1 text-[11px] text-term-muted">
        Per-market benchmarks (ASPI-style) plus Top-20-only composites from live bars — never hardcoded. Charts load on
        expand so the homepage stays fast.
      </p>
      <div key={tick} className="mt-3 grid min-w-0 gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {ASPI_MICS.map((mic) => (
          <MemoMarketIndexCard key={`${mic}-${tick}`} mic={mic} userId={userId} tier={tier} defaultOpen={all} />
        ))}
        {ALL_ASPI_MICS.filter((m) => !ASPI_MICS.includes(m)).map((mic) => (
          <MemoDisabledMarketCard key={`${mic}-${tick}`} mic={mic} />
        ))}
      </div>
      {/* welcome-page teaser slot: disabled venues render as explicit disabled
          cards, never as data. Future per-user pinned indices (userId/tier)
          plug in here — no auth implemented. */}
      <p className="mt-2 text-[10px] text-term-muted">
        Benchmarks track their venue only. Top-20 lines are equal-weighted, rebased to 100 — not investment advice.
      </p>
    </section>
  );
}

export { AspiChart, IndexLineSvg, MarketIndicesSection, Top20AspiChart };
export default MarketIndicesSection;
