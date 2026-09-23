import React, { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AI_PROFILES,
  api,
  extractBackendDetail,
  getAIPerformance,
  getHealth,
  getProviderBudgets,
  getProviderKeysStatus,
  getProvidersHealth,
  testProviderHealth,
} from "../../api/client";
import Skeleton from "../../components/Skeleton";
import EmptyState from "../../components/EmptyState";
import ErrorState from "../../components/ErrorState";
import { formatDateTime } from "../../utils/format";

const PROVIDER_OPTIONS = [
  { value: "gemini", label: "gemini" },
  { value: "openai", label: "openai" },
  { value: "anthropic", label: "claude" },
  { value: "xai", label: "grok" },
];
const PROFILE_HINTS = {
  "Quick Insight": "default: Gemini 3.7 Flash · 1–2 sentence take",
  "Deep Research": "multi-source structured brief · higher budget",
  "Forecast Assist": "bounded 21d opinion · capped 20% influence",
  Report: "scheduled / on-demand full report",
};
const PROFILE_DEFAULT_MODEL = {
  "Quick Insight": "gemini-3.7-flash",
  "Deep Research": "gemini-3.7-flash",
  "Forecast Assist": "gemini-3.7-flash",
  Report: "gemini-3.7-flash",
};
const MAX_PERF_ROWS = 100;

function timeAgo(iso) {
  try {
    const ms = Date.parse(iso);
    if (!Number.isFinite(ms)) return "timestamp unavailable";
    const secs = Math.max(0, Math.round((Date.now() - ms) / 1000));
    if (secs < 60) return `Updated ${secs}s ago`;
    const mins = Math.round(secs / 60);
    if (mins < 60) return `Updated ${mins}m ago`;
    const hrs = Math.round(mins / 60);
    if (hrs < 24) return `Updated ${hrs}h ago`;
    return `Updated ${Math.round(hrs / 24)}d ago`;
  } catch {
    return "timestamp unavailable";
  }
}

// Operational status: icon + text + timestamp, never color alone.
// Healthy / Delayed / Unavailable / Degraded each render distinctly.
function operationalStatus(rawStatus, rawState, circuit) {
  const s = String(rawStatus ?? rawState ?? "").trim().toLowerCase();
  const st = String(rawState ?? "").trim().toLowerCase();
  const c = String(circuit ?? "").trim().toLowerCase();
  if (s === "ok" || s === "up") return { label: "Healthy", icon: "●", cls: "text-term-green", dot: "bg-term-green" };
  if (s === "degraded" || c === "open") return { label: "Degraded", icon: "▲", cls: "text-term-amber", dot: "bg-term-amber" };
  if (s === "delayed") return { label: "Delayed", icon: "◌", cls: "text-term-amber", dot: "bg-term-amber" };
  if (s === "down") return { label: "Unavailable", icon: "✕", cls: "text-term-red", dot: "bg-term-red" };
  if (s === "unknown" || s === "unconfigured" || s === "" || st === "unknown") {
    return { label: "Unavailable", icon: "✕", cls: "text-term-muted", dot: "bg-term-muted" };
  }
  return { label: "Degraded", icon: "▲", cls: "text-term-amber", dot: "bg-term-amber" };
}

function freshnessFor(lastCheck, latencyMs) {
  if (!lastCheck) return { label: "Delayed", icon: "◌", cls: "text-term-amber" };
  try {
    const ms = Date.parse(lastCheck);
    if (!Number.isFinite(ms)) return { label: "Delayed", icon: "◌", cls: "text-term-amber" };
    const ageSec = (Date.now() - ms) / 1000;
    if (ageSec <= 90 && typeof latencyMs === "number" && Number.isFinite(latencyMs)) {
      return { label: "Fresh", icon: "●", cls: "text-term-green" };
    }
    if (ageSec <= 600) return { label: "Fresh", icon: "●", cls: "text-term-green" };
    return { label: "Delayed", icon: "◌", cls: "text-term-amber" };
  } catch {
    return { label: "Delayed", icon: "◌", cls: "text-term-amber" };
  }
}

function ProviderSettings() {
  const [provider, setProvider] = useState("gemini");
  const [model, setModel] = useState("gemini-3.7-flash");
  const [apiKey, setApiKey] = useState("");
  const [profile, setProfile] = useState("Quick Insight");
  const [budget, setBudget] = useState("25");
  // Dual health paths preserved: /health (overview) + /api/providers/health
  // (detailed per-provider). Detailed list wins when present; overview is
  // the fallback so one missing endpoint never blanks the table.
  const health = useQuery({
    queryKey: ["health"],
    queryFn: ({ signal }) => getHealth({ signal }),
    retry: false,
    staleTime: 30000,
  });
  const detailedHealth = useQuery({
    queryKey: ["providers-health"],
    queryFn: ({ signal }) => getProvidersHealth({ signal }),
    retry: false,
    staleTime: 30000,
  });
  const keyStatus = useQuery({
    queryKey: ["provider-keys-status"],
    queryFn: getProviderKeysStatus,
    retry: false,
    staleTime: 30000,
  });
  const savedBudgets = useQuery({
    queryKey: ["provider-budgets"],
    queryFn: getProviderBudgets,
    retry: false,
    staleTime: 60000,
  });
  const perf = useQuery({
    queryKey: ["ai-performance"],
    queryFn: getAIPerformance,
    retry: false,
    staleTime: 60000,
  });
  const queryClient = useQueryClient();
  const save = useMutation({
    mutationFn: async () => {
      const { data } = await api.post("/api/providers/keys", { provider, model, api_key: apiKey });
      return data;
    },
    onSuccess: () => {
      setApiKey("");
      void queryClient.invalidateQueries({ queryKey: ["provider-keys-status"] });
    },
  });
  const test = useMutation({
    // AI key check: POST /api/ai/providers/health/test (configured vs stub).
    mutationFn: () => testProviderHealth(provider),
  });
  const saveBudget = useMutation({
    mutationFn: async () => {
      const monthly_usd = Number(budget);
      if (!Number.isFinite(monthly_usd) || monthly_usd < 0) throw new Error("invalid budget");
      const { data } = await api.post("/api/providers/budget", { provider, monthly_usd });
      return data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["provider-budgets"] });
    },
  });
  function pickProfile(p) {
    setProfile(p);
    setModel(PROFILE_DEFAULT_MODEL[p]);
  }

  const healthRows = useMemo(() => {
    const detailed = Array.isArray(detailedHealth.data) ? detailedHealth.data : [];
    if (detailed.length > 0) {
      return {
        source: "detailed /api/providers/health",
        rows: detailed.map((r) => ({
          name: r.name ?? r.provider ?? "unknown",
          status: r.status ?? r.state ?? "unknown",
          state: r.state,
          circuit: r.circuit,
          latency_ms: r.latency_ms ?? r.latency_p50_ms,
          latency_p95: r.latency_p95_ms,
          error_rate: r.error_rate_1h,
          calls: r.calls_1h ?? r.total_calls,
          last_check: r.last_check ?? null,
        })),
      };
    }
    const overview = Array.isArray(health.data?.providers) ? health.data.providers : [];
    return {
      source: "overview /health",
      rows: overview.map((r) => ({
        name: r.name ?? "unknown",
        status: r.status ?? "unknown",
        state: undefined,
        circuit: undefined,
        latency_ms: r.latency_ms,
        latency_p95: undefined,
        error_rate: undefined,
        calls: undefined,
        last_check: r.last_check ?? null,
      })),
    };
  }, [detailedHealth.data, health.data]);

  const healthLoading = health.isLoading || detailedHealth.isLoading;
  const healthError = health.isError && detailedHealth.isError;

  return (
    <div className="min-w-0 space-y-4">
      {/* Operational Data Health table — no decorative charts. */}
      <section className="term-panel min-w-0 p-4" aria-labelledby="data-health">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 id="data-health" className="term-label">Data health · Provider / Status / Last update / Latency / Freshness / Quality</h2>
          <span className="term-num text-[10px] text-term-muted" title="Which health endpoint served this table">
            via {healthRows.source}
          </span>
        </div>
        <p className="mt-1 text-[11px] text-term-muted">
          Operational view — each row carries icon + text + timestamp, never color alone.
        </p>
        {healthLoading && (
          <div className="mt-2"><Skeleton label="checking providers…" lines={4} variant="table" /></div>
        )}
        {healthError && (
          <div className="mt-2">
            <ErrorState
              title="Provider health unavailable"
              detail={`provider didn't return fresh data (${extractBackendDetail(health.error ?? detailedHealth.error, "health endpoints unreachable")}) — keys can still be saved below`}
              onRetry={() => {
                void health.refetch();
                void detailedHealth.refetch();
              }}
            />
          </div>
        )}
        {!healthLoading && !healthError && healthRows.rows.length === 0 && (
          <div className="mt-2">
            <EmptyState
              title="No provider health rows yet"
              detail="What: health table empty. Why: backends returned no provider list. Next: retry, or save a provider key below — health accrues after the first probe."
              actionLabel="Retry health check"
              onAction={() => {
                void health.refetch();
                void detailedHealth.refetch();
              }}
            />
          </div>
        )}
        {!healthLoading && healthRows.rows.length > 0 && (
          <div className="mt-2 overflow-x-auto">
            <table className="w-full min-w-[640px] text-xs">
              <caption className="sr-only">Provider health: status, last update, latency, freshness and quality</caption>
              <thead>
                <tr className="text-left text-term-muted">
                  <th scope="col" className="py-1 pr-2">Provider</th>
                  <th scope="col" className="py-1 pr-2">Status</th>
                  <th scope="col" className="py-1 pr-2">Last update</th>
                  <th scope="col" className="py-1 pr-2 text-right">Latency</th>
                  <th scope="col" className="py-1 pr-2">Freshness</th>
                  <th scope="col" className="py-1 text-right">Quality</th>
                </tr>
              </thead>
              <tbody>
                {healthRows.rows.map((pr) => {
                  const op = operationalStatus(pr.status, pr.state, pr.circuit);
                  const fresh = freshnessFor(pr.last_check, pr.latency_ms);
                  const latencyText =
                    typeof pr.latency_ms === "number" && Number.isFinite(pr.latency_ms)
                      ? `${Math.round(pr.latency_ms)}ms`
                      : "—";
                  const qualityText =
                    typeof pr.error_rate === "number" && Number.isFinite(pr.error_rate)
                      ? `err ${(pr.error_rate * 100).toFixed(1)}%`
                      : typeof pr.calls === "number" && Number.isFinite(pr.calls)
                        ? `${pr.calls} calls`
                        : "—";
                  const lastUpdateTitle = pr.last_check ? `${formatDateTime(pr.last_check)}` : "no timestamp reported";
                  return (
                    <tr key={pr.name} className="border-t border-term-border even:bg-term-panel2">
                      <td className="py-1 pr-2 font-bold text-term-text">{pr.name}</td>
                      <td className="py-1 pr-2" title={lastUpdateTitle}>
                        <span className={`inline-flex items-center gap-1.5 font-bold ${op.cls}`}>
                          <span aria-hidden="true">{op.icon}</span>
                          <span>{op.label}</span>
                        </span>
                        <span className="term-num ml-2 text-[10px] text-term-muted">{String(pr.status)}</span>
                      </td>
                      <td className="term-num py-1 pr-2 text-term-muted" title={lastUpdateTitle}>
                        {pr.last_check ? timeAgo(pr.last_check) : "never reported"}
                      </td>
                      <td className="term-num py-1 pr-2 text-right text-term-text" title={typeof pr.latency_p95 === "number" ? `p95 ${Math.round(pr.latency_p95)}ms` : latencyText}>
                        {latencyText}
                      </td>
                      <td className="py-1 pr-2" title={lastUpdateTitle}>
                        <span className={`inline-flex items-center gap-1.5 ${fresh.cls}`}>
                          <span aria-hidden="true">{fresh.icon}</span>
                          <span>{fresh.label}</span>
                        </span>
                      </td>
                      <td className="term-num py-1 text-right text-term-muted">{qualityText}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        {!healthLoading && (
          <p className="mt-2 text-[10px] text-term-muted">
            Example row reads: Alpaca <span className="text-term-green">●Healthy</span> Updated 12s ago Freshness 98% Latency 142ms.
            States: <span className="text-term-green">●Healthy</span> / <span className="text-term-amber">◌Delayed</span> / <span className="text-term-muted">✕Unavailable</span> / <span className="text-term-amber">▲Degraded</span> — each with timestamp.
          </p>
        )}
      </section>

      <div className="grid min-w-0 grid-cols-1 gap-4 md:grid-cols-2">
        <section className="term-panel min-w-0 p-4" aria-labelledby="ps-keys">
          <h2 id="ps-keys" className="term-label">Provider keys (encrypted at rest, never exposed)</h2>
          <label className="mt-2 block text-xs text-term-muted" htmlFor="ps-provider">Provider</label>
          <select id="ps-provider" className="term-input mt-1 w-full" value={provider} onChange={(e) => setProvider(e.target.value)}>
            {PROVIDER_OPTIONS.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
          </select>
          <label className="mt-2 block text-xs text-term-muted" htmlFor="ps-model">Model</label>
          <input id="ps-model" className="term-input mt-1 w-full" value={model} onChange={(e) => setModel(e.target.value)} placeholder="gemini-3.7-flash" spellCheck={false} />
          <label className="mt-2 block text-xs text-term-muted" htmlFor="ps-key">API key</label>
          <input id="ps-key" className="term-input mt-1 w-full" type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder="paste key — sent once, stored encrypted" autoComplete="off" />
          <div className="mt-3 flex flex-wrap gap-2">
            <button className="term-btn text-xs" type="button" disabled={!apiKey || save.isPending} onClick={() => save.mutate()}>
              {save.isPending ? "SAVING…" : "SAVE KEY"}
            </button>
            <button className="term-btn-ghost text-xs" type="button" disabled={test.isPending} onClick={() => test.mutate()}>
              {test.isPending ? "TESTING…" : "⟳ HEALTH TEST"}
            </button>
          </div>
          {save.isSuccess && <p className="mt-2 text-xs text-term-green">Key stored (server confirms receipt only).</p>}
          {save.isError && <p className="mt-2 text-xs text-term-red" role="alert">Save failed — backend unreachable or rejected.</p>}
          {keyStatus.isLoading && <p className="mt-2 text-xs text-term-muted" role="status">loading key status…</p>}
          {keyStatus.isError && <p className="mt-2 text-xs text-term-amber" role="status">⚠ key status unavailable ({keyStatus.error instanceof Error ? keyStatus.error.message : "backend unreachable"}) — keys can still be saved.</p>}
          {!keyStatus.isLoading && !keyStatus.isError && (!keyStatus.data || keyStatus.data.length === 0) && (
            <p className="mt-2 text-xs text-term-muted" role="status">No provider keys configured yet.</p>
          )}
          {keyStatus.data && keyStatus.data.length > 0 && (
            <ul className="mt-2 space-y-1 text-xs" aria-label="Configured providers">
              {keyStatus.data.map((k) => (
                <li key={k.provider} className="flex justify-between gap-2 border-b border-term-border pb-1">
                  <span className="text-term-muted">{k.provider}</span>
                  <span className={k.configured ? "text-term-green" : "text-term-muted"}>
                    {k.configured ? `✓ configured${k.model ? ` · ${k.model}` : ""}` : "○ no key"}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {test.data && (
            <p className={`mt-2 text-xs ${test.data.ok ? "text-term-green" : "text-term-red"}`}>
              Test: {test.data.ok ? "✓ OK" : "✕ FAIL"} {test.data.latency_ms !== undefined ? `· ${test.data.latency_ms}ms` : ""} {test.data.message ?? ""}
            </p>
          )}
          {test.isError && <p className="mt-2 text-xs text-term-red">Health test failed to run ({test.error instanceof Error ? test.error.message : "backend unreachable"}).</p>}
        </section>

        <section className="term-panel min-w-0 p-4" aria-labelledby="ps-profiles">
          <h2 id="ps-profiles" className="term-label">Task profiles (AI weight capped, fixed for v1)</h2>
          <div className="mt-2 space-y-1" role="radiogroup" aria-label="AI task profile">
            {AI_PROFILES.map((p) => (
              <label key={p} className="flex cursor-pointer items-center gap-2 text-sm">
                <input type="radio" name="profile" checked={profile === p} onChange={() => pickProfile(p)} />
                <span>{p}</span>
                <span className="text-[10px] text-term-muted">· {PROFILE_HINTS[p]}</span>
              </label>
            ))}
          </div>
          <label className="mt-4 block text-xs text-term-muted" htmlFor="ps-budget">Monthly budget cap (USD) · {provider}</label>
          <div className="mt-1 flex gap-2">
            <input id="ps-budget" className="term-input w-32" inputMode="decimal" value={budget} onChange={(e) => setBudget(e.target.value)} placeholder="25" />
            <button className="term-btn-ghost text-xs" type="button" disabled={saveBudget.isPending} onClick={() => saveBudget.mutate()}>
              {saveBudget.isPending ? "SAVING…" : "SAVE BUDGET"}
            </button>
          </div>
          {savedBudgets.isLoading && <p className="mt-1 text-xs text-term-muted" role="status">loading saved budgets…</p>}
          {savedBudgets.isError && <p className="mt-1 text-xs text-term-amber" role="status">⚠ saved budgets unavailable ({savedBudgets.error instanceof Error ? savedBudgets.error.message : "backend unreachable"}).</p>}
          {saveBudget.isSuccess && <p className="mt-1 text-xs text-term-green">Budget saved.</p>}
          {savedBudgets.data && savedBudgets.data[provider] !== undefined && (
            <p className="term-num mt-1 text-xs text-term-muted">Saved cap for {provider}: ${savedBudgets.data[provider]} USD/mo.</p>
          )}
          {saveBudget.isError && (
            String(saveBudget.error?.message ?? "") === "invalid budget"
              ? <p className="mt-1 text-xs text-term-amber">Enter a non-negative number.</p>
              : <p className="mt-1 text-xs text-term-amber">Budget endpoint not confirmed by backend — not saved; value retained in this field only ({budget} USD).</p>
          )}
        </section>
      </div>

      <section className="term-panel min-w-0 p-4" aria-labelledby="ps-perf">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 id="ps-perf" className="term-label">Historical provider performance · by exchange + horizon</h2>
          <button className="term-btn-ghost text-xs" type="button" disabled={perf.isFetching} onClick={() => perf.refetch()}>
            {perf.isFetching ? "REFRESHING…" : "⟳ REFRESH"}
          </button>
        </div>
        {perf.isLoading && <div className="mt-2"><Skeleton label="loading provider performance…" lines={4} variant="table" /></div>}
        {perf.isError && <p className="mt-2 text-xs text-term-amber">⚠ Performance endpoint unreachable ({perf.error instanceof Error ? perf.error.message : "unknown error"}) — table unavailable, settings above unaffected.</p>}
        {perf.data && perf.data.length === 0 && <p className="mt-2 text-xs text-term-muted">No historical scores yet — performance accrues as bounded AI opinions resolve out-of-sample.</p>}
        {perf.data && perf.data.length > 0 && (
          <div className="mt-2 overflow-x-auto">
            {perf.data.length > MAX_PERF_ROWS && (
              <p className="mb-1 text-[11px] text-term-muted" role="status">showing first {MAX_PERF_ROWS} of {perf.data.length} — refresh narrows to the latest window.</p>
            )}
            <table className="w-full text-xs">
              <caption className="sr-only">Historical provider performance</caption>
              <thead>
                <tr className="text-left text-term-muted">
                  <th scope="col" className="py-1 pr-2">Provider</th>
                  <th scope="col" className="py-1 pr-2">Model</th>
                  <th scope="col" className="py-1 pr-2">Exch</th>
                  <th scope="col" className="py-1 pr-2 text-right">Hor</th>
                  <th scope="col" className="py-1 pr-2 text-right">n</th>
                  <th scope="col" className="py-1 pr-2 text-right">Brier</th>
                  <th scope="col" className="py-1 pr-2 text-right">ECE</th>
                  <th scope="col" className="py-1 pr-2 text-right">Hit rate</th>
                </tr>
              </thead>
              <tbody>
                {perf.data.slice(0, MAX_PERF_ROWS).map((row, i) => (
                  <tr key={`${row.provider}-${row.model}-${i}`} className="border-t border-term-border even:bg-term-panel2">
                    <td className="py-1 pr-2">{row.provider}</td>
                    <td className="max-w-[140px] truncate py-1 pr-2 text-term-muted" title={row.model}>{row.model || "—"}</td>
                    <td className="py-1 pr-2 text-term-muted">{row.exchange || "—"}</td>
                    <td className="term-num py-1 pr-2 text-right">{row.horizon_days ?? "—"}</td>
                    <td className="term-num py-1 pr-2 text-right">{row.n_calls ?? "—"}</td>
                    <td className="term-num py-1 pr-2 text-right">{row.brier === null || row.brier === undefined ? "—" : Number(row.brier).toFixed(4)}</td>
                    <td className="term-num py-1 pr-2 text-right">{row.ece === null || row.ece === undefined ? "—" : Number(row.ece).toFixed(4)}</td>
                    <td className="term-num py-1 pr-2 text-right">{row.hit_rate === null || row.hit_rate === undefined ? "—" : Number(row.hit_rate).toFixed(3)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

export { ProviderSettings as default };
