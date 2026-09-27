// Phase 8: premarket sentiment strip (frontend only).
// Fetches GET /api/sentiment/premarket best-effort and renders the aggregate
// mood + source counts. Fail-hidden by contract: loading, 404/disabled, empty,
// or any error returns null so HomePage is unaffected. Never throws into the
// render path; no new deps. Futures/movers render only when the payload
// carries them (backend shape varies by enabled sources).
import React from "react";
import { useQuery } from "@tanstack/react-query";
import { getPremarket } from "../api/news";

function moodOf(v) {
  if (typeof v !== "number" || !Number.isFinite(v)) return { word: "NEUTRAL", cls: "text-term-muted" };
  if (v > 0.2) return { word: "BULLISH", cls: "text-term-green" };
  if (v < -0.2) return { word: "BEARISH", cls: "text-term-red" };
  return { word: "NEUTRAL", cls: "text-term-muted" };
}

function topHeadlines(data) {
  // Best-effort movers: prefer Alpaca articles, fall back to Investopedia.
  try {
    const pools = [
      ...(Array.isArray(data?.alpaca?.articles) ? data.alpaca.articles : []),
      ...(Array.isArray(data?.investopedia?.articles) ? data.investopedia.articles : []),
    ];
    return pools
      .filter((a) => a && typeof a.title === "string" && a.title.trim() !== "")
      .slice(0, 3)
      .map((a) => ({
        title: String(a.title).slice(0, 120),
        url: typeof a.url === "string" ? a.url : "",
        label: String(a.sentiment_label ?? "").toLowerCase(),
      }));
  } catch {
    return [];
  }
}

function PremarketStrip() {
  const q = useQuery({
    queryKey: ["premarket"],
    queryFn: ({ signal }) => getPremarket({ signal }),
    retry: false,
    staleTime: 60000,
  });
  // Fail-hidden: loading / error / disabled / empty all unmount silently.
  if (q.isLoading || q.isError || !q.data) return null;
  const d = q.data;
  const hasSignal = d.articleCount > 0 || d.sources.length > 0;
  if (!hasSignal) return null;
  const mood = moodOf(d.meanSentiment);
  const heads = topHeadlines(d.raw ?? d);
  const futures = Array.isArray(d.raw?.futures) ? d.raw.futures.slice(0, 4) : [];
  return (
    <section className="term-panel min-w-0 p-4" aria-labelledby="premarket-h">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="premarket-h" className="term-label">
          Premarket mood
        </h2>
        <span className="tnum text-[11px] text-term-muted" role="status">
          {d.articleCount} articles{d.sources.length > 0 ? ` · ${d.sources.join(" + ")}` : ""}
        </span>
      </div>
      <div className="mt-1 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className={`text-sm font-extrabold ${mood.cls}`}>
          {mood.word === "BULLISH" ? "▲" : mood.word === "BEARISH" ? "▼" : "→"} {mood.word}
        </span>
        <span className="tnum term-num text-sm text-term-text">
          {Number.isFinite(d.meanSentiment) ? d.meanSentiment.toFixed(2) : "—"}
        </span>
        <span className="text-[11px] text-term-muted">
          {d.bullishCount} bullish · {d.bearishCount} bearish
        </span>
        <button className="term-btn-sm shrink-0" type="button" onClick={() => void q.refetch()} aria-label="Refresh premarket mood">
          REFRESH
        </button>
      </div>
      {futures.length > 0 && (
        <ul className="mt-2 flex flex-wrap gap-2" aria-label="Premarket futures">
          {futures.map((f, i) => (
            <li key={`${f?.symbol ?? i}`} className="tnum rounded border border-term-border px-2 py-1 text-[11px] text-term-text">
              {String(f?.symbol ?? f?.name ?? `FUT${i + 1}`)} {f?.change_pct != null ? `${Number(f.change_pct).toFixed(2)}%` : ""}
            </li>
          ))}
        </ul>
      )}
      {heads.length > 0 && (
        <ul className="mt-2 space-y-1" aria-label="Premarket headlines">
          {heads.map((h, i) => (
            <li key={i} className="truncate text-[11px] text-term-muted">
              {h.url ? (
                <a href={h.url} target="_blank" rel="noreferrer" className="hover:text-term-text hover:underline">
                  {h.title}
                </a>
              ) : (
                <span>{h.title}</span>
              )}
              {h.label && h.label !== "neutral" && <span className="ml-1 text-[10px]">· {h.label}</span>}
            </li>
          ))}
        </ul>
      )}
      {d.disclosure && <p className="mt-1 text-[10px] text-term-muted">{d.disclosure}</p>}
    </section>
  );
}

export { PremarketStrip as default, PremarketStrip };
