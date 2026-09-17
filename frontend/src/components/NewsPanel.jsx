import React from "react";
import Skeleton from "./Skeleton";
import ErrorState from "./ErrorState";

function sentimentColor(label) {
  if (label === "bullish") return "text-term-green";
  if (label === "bearish") return "text-term-red";
  return "text-term-muted";
}

function timeAgo(iso) {
  try {
    const ms = Date.parse(iso);
    if (!Number.isFinite(ms)) return "";
    const mins = Math.max(0, Math.round((Date.now() - ms) / 60000));
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.round(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.round(hrs / 24)}d ago`;
  } catch {
    return "";
  }
}

function NewsPanel({ data, isLoading, isError, error, onRetry }) {
  const articles = data?.articles ?? [];
  return (
    <div>
      <p className="text-[11px] text-term-muted">
        Fresh headlines from Alpaca News (US stocks). Mood is counted from words like “beats” vs “misses” — simple and explainable.
      </p>
      {isLoading && <div className="mt-2"><Skeleton label="loading market news…" lines={4} /></div>}
      {isError && (
        <div className="mt-2">
          <ErrorState
            title="News unavailable"
            detail={
              error?.response?.status === 423 || /423/.test(String(error?.message ?? ""))
                ? "Add Alpaca keys on the backend (ALPACA_API_KEY_ID + ALPACA_API_SECRET_KEY) to unlock news."
                : error instanceof Error ? error.message : "News endpoint unreachable."
            }
            onRetry={onRetry}
          />
        </div>
      )}
      {!isLoading && !isError && articles.length === 0 && (
        <p className="mt-2 text-xs text-term-muted" role="status">No headlines right now — check back after the next market session.</p>
      )}
      {!isLoading && !isError && articles.length > 0 && (
        <ul className="mt-2 space-y-2">
          {articles.slice(0, 12).map((a, i) => (
            <li key={`${a.url || a.title}-${i}`} className="border-b border-term-border pb-2">
              <div className="flex flex-wrap items-center gap-2 text-[11px]">
                <span className={`font-bold uppercase ${sentimentColor(a.sentiment_label)}`}>
                  {a.sentiment_label === "bullish" ? "▲ Positive" : a.sentiment_label === "bearish" ? "▼ Negative" : "● Neutral"}
                </span>
                {a.symbols?.length > 0 && (
                  <span className="text-term-muted">{a.symbols.slice(0, 4).join(" · ")}</span>
                )}
                {a.created_at && <span className="text-term-muted">{timeAgo(a.created_at)}</span>}
              </div>
              {a.url ? (
                <a href={a.url} target="_blank" rel="noreferrer" className="mt-0.5 block text-sm font-semibold text-term-text hover:text-term-green hover:underline">
                  {a.title}
                </a>
              ) : (
                <p className="mt-0.5 text-sm font-semibold text-term-text">{a.title}</p>
              )}
              {a.summary && <p className="mt-0.5 line-clamp-2 text-xs text-term-muted">{a.summary}</p>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export { NewsPanel as default };
