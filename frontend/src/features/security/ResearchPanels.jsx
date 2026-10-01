import React, { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Sparkles, TriangleAlert } from "lucide-react";
import Card from "../../components/ui/Card";
import Badge from "../../components/ui/Badge";
import Segmented from "../../components/ui/Segmented";
import NewsPanel from "../../components/NewsPanel";
import { AI_PROFILES, friendlyAIError, postAIInsight, tryNormalizeAIOpinion } from "../../api/client";
import { fmtPct, formatMetric } from "../../utils/format";

const GROUPS = [
  { key: "fundamentals", title: "Fundamentals", subtitle: "Growth, margins and returns from filed statements" },
  { key: "valuation", title: "Valuation", subtitle: "Cost of capital and value estimates" },
  { key: "quality", title: "Quality", subtitle: "Piotroski, Altman Z, Beneish M, DuPont" },
  { key: "technical", title: "Technical", subtitle: "Trend and momentum indicators (latest values)" },
];

function humanize(key) {
  const s = key.replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function MetricGroup({ title, subtitle, data }) {
  const [showAll, setShowAll] = useState(false);
  const rows = Object.entries(data ?? {}).map(([k, v]) => ({ k, m: formatMetric(v) }));
  const available = rows.filter((r) => !r.m.unavailable);
  const hidden = rows.length - available.length;
  const shown = showAll ? rows : available;
  return (
    <Card title={title} subtitle={subtitle} actions={hidden > 0 ? <button type="button" className="term-btn-sm" onClick={() => setShowAll((v) => !v)}>{showAll ? "Hide unavailable" : `+${hidden} unavailable`}</button> : null}>
      {shown.length === 0 ? (
        <p className="text-sm text-term-muted">No values for this group.</p>
      ) : (
        <dl className="divide-y divide-term-border/60">
          {shown.map(({ k, m }) => (
            <div key={k} className="flex items-start justify-between gap-4 py-1.5 text-sm" title={m.title || undefined}>
              <dt className="text-term-muted">{humanize(k)}</dt>
              <dd className={`term-num max-w-[60%] break-words text-right ${m.unavailable ? "text-term-faint" : "text-term-text"}`}>{m.text}</dd>
            </div>
          ))}
        </dl>
      )}
    </Card>
  );
}

function FundamentalsTab({ analytics, loading, error, onRetry, events = [] }) {
  if (loading) return <div className="h-80 animate-pulse rounded-lg bg-term-panel" />;
  if (error || !analytics) {
    return (
      <Card title="Fundamentals unavailable">
        <p className="text-sm text-term-muted">{error || "No analytics returned."}</p>
        {onRetry ? <button type="button" className="term-btn-ghost mt-3 text-sm" onClick={onRetry}>Retry</button> : null}
      </Card>
    );
  }
  return (
    <div className="space-y-4">
      {analytics.note ? (
        <p className="rounded-md border border-term-border bg-term-panel px-3 py-2 text-xs text-term-muted">{analytics.note}</p>
      ) : null}
      <div className="grid gap-4 lg:grid-cols-2">
        {GROUPS.map((g) => <MetricGroup key={g.key} title={g.title} subtitle={g.subtitle} data={analytics[g.key]} />)}
      </div>
      {events.length > 0 ? (
        <Card title="Events" subtitle="Filings and corporate events">
          <ul className="divide-y divide-term-border/60 text-sm">
            {events.map((e, i) => (
              <li key={`${e.date}-${i}`} className="flex justify-between gap-3 py-1.5">
                <span className="min-w-0 text-term-text">{e.title}</span>
                <span className="term-num shrink-0 text-term-muted">{e.date || "—"}</span>
              </li>
            ))}
          </ul>
        </Card>
      ) : null}
    </div>
  );
}

function NewsTab({ news, loading, error, onRetry }) {
  return (
    <Card title="News" subtitle="Headlines mentioning this symbol">
      <NewsPanel data={news} isLoading={loading} isError={Boolean(error)} error={error} onRetry={onRetry} />
    </Card>
  );
}

function AITab({ symbol }) {
  const [profile, setProfile] = useState(AI_PROFILES[0]);
  const m = useMutation({ mutationFn: () => postAIInsight(symbol, profile) });
  const op = m.data ? tryNormalizeAIOpinion(m.data) : null;
  return (
    <div className="space-y-4">
      <Card
        title="AI research assistant"
        subtitle="Summarizes catalysts and risks from the evidence the app has. Runs only when you ask; counts toward your daily AI limit."
        actions={<Segmented options={AI_PROFILES.map((p) => ({ value: p, label: p }))} value={profile} onChange={setProfile} ariaLabel="AI profile" size="xs" />}
      >
        <button type="button" className="term-btn" disabled={m.isPending} onClick={() => m.mutate()}>
          <Sparkles className="h-4 w-4" aria-hidden="true" />
          {m.isPending ? "Thinking…" : op ? "Ask again" : `Run ${profile}`}
        </button>
        {m.isError ? (
          <p className="mt-3 flex items-start gap-2 text-sm text-term-amber"><TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />{friendlyAIError(m.error)}</p>
        ) : null}
      </Card>
      {op ? (
        <Card
          title={`${op.provider ?? "AI"}${op.model ? ` · ${op.model}` : ""}`}
          subtitle={`${op.time_horizon_days}-day view · ${op.evidence_ids?.length ?? 0} pieces of evidence cited`}
          actions={<Badge tone="warning">AI opinion · unverified</Badge>}
        >
          <p className="text-sm text-term-text">
            Leans <b>{op.direction}</b> with {fmtPct(op.probability, 0)} confidence. AI views are not part of the measured forecast and have no track record here.
          </p>
          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <div>
              <div className="mb-1.5 text-2xs font-medium uppercase tracking-[0.08em] text-term-muted">Catalysts</div>
              <ul className="list-disc space-y-1 pl-5 text-sm text-term-text">{(op.catalysts ?? []).map((c) => <li key={c}>{c}</li>)}</ul>
            </div>
            <div>
              <div className="mb-1.5 text-2xs font-medium uppercase tracking-[0.08em] text-term-muted">Risks</div>
              <ul className="list-disc space-y-1 pl-5 text-sm text-term-text">{(op.risks ?? []).map((c) => <li key={c}>{c}</li>)}</ul>
            </div>
          </div>
          {(op.limitations ?? []).length ? (
            <p className="mt-4 text-xs text-term-muted">Limitations: {op.limitations.join(" · ")}</p>
          ) : null}
        </Card>
      ) : null}
    </div>
  );
}

export { AITab, FundamentalsTab, NewsTab };
