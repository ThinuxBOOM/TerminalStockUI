import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AI_PROFILES,
  api,
  getAIPerformance,
  getHealth,
  getProviderBudgets,
  getProviderKeysStatus,
  testProviderHealth,
  type AIProfile,
} from '../../api/client';
import Loading from '../../components/Loading';

/** API value → display label. Backend uses anthropic/xai; UI shows claude/grok. */
const PROVIDER_OPTIONS = [
  { value: 'gemini', label: 'gemini' },
  { value: 'openai', label: 'openai' },
  { value: 'anthropic', label: 'claude' },
  { value: 'xai', label: 'grok' },
] as const;
type ProviderValue = (typeof PROVIDER_OPTIONS)[number]['value'];

const PROFILE_HINTS: Record<AIProfile, string> = {
  'Quick Insight': 'default: Gemini 3.7 Flash · 1–2 sentence take',
  'Deep Research': 'multi-source structured brief · higher budget',
  'Forecast Assist': 'bounded 21d opinion · capped 20% influence',
  Report: 'scheduled / on-demand full report',
};

const PROFILE_DEFAULT_MODEL: Record<AIProfile, string> = {
  'Quick Insight': 'gemini-3.7-flash',
  'Deep Research': 'gemini-3.7-flash',
  'Forecast Assist': 'gemini-3.7-flash',
  Report: 'gemini-3.7-flash',
};

export default function ProviderSettings() {
  const [provider, setProvider] = useState<ProviderValue>('gemini');
  const [model, setModel] = useState('gemini-3.7-flash');
  const [apiKey, setApiKey] = useState('');
  const [profile, setProfile] = useState<AIProfile>('Quick Insight');
  const [budget, setBudget] = useState('25');
  const health = useQuery({ queryKey: ['health'], queryFn: getHealth, retry: false });
  const keyStatus = useQuery({
    queryKey: ['provider-keys-status'],
    queryFn: getProviderKeysStatus,
    retry: false,
    staleTime: 30_000,
  });
  const savedBudgets = useQuery({
    queryKey: ['provider-budgets'],
    queryFn: getProviderBudgets,
    retry: false,
    staleTime: 60_000,
  });
  const perf = useQuery({
    queryKey: ['ai-performance'],
    queryFn: getAIPerformance,
    retry: false,
    staleTime: 60_000,
  });

  const queryClient = useQueryClient();

  const save = useMutation({
    mutationFn: async () => {
      // Keys are stored encrypted server-side; never returned or logged.
      const { data } = await api.post('/api/providers/keys', { provider, model, api_key: apiKey });
      return data;
    },
    onSuccess: () => {
      setApiKey('');
      void queryClient.invalidateQueries({ queryKey: ['provider-keys-status'] });
    },
  });
  const test = useMutation({
    // AI key check: POST /api/ai/providers/health/test (configured vs stub).
    mutationFn: () => testProviderHealth(provider),
  });
  const saveBudget = useMutation({
    mutationFn: async () => {
      const monthly_usd = Number(budget);
      if (!Number.isFinite(monthly_usd) || monthly_usd < 0) throw new Error('invalid budget');
      const { data } = await api.post('/api/providers/budget', { provider, monthly_usd });
      return data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['provider-budgets'] });
    },
  });

  function pickProfile(p: AIProfile) {
    setProfile(p);
    setModel(PROFILE_DEFAULT_MODEL[p]);
  }

  return (
    <div className="space-y-4">
      <div className="grid gap-4 md:grid-cols-2">
        <section className="term-panel p-4">
          <p className="term-label">Provider keys (encrypted at rest, never exposed)</p>
          <label className="mt-2 block text-xs text-term-muted" htmlFor="ps-provider">
            Provider
          </label>
          <select
            id="ps-provider"
            className="term-input w-full"
            value={provider}
            onChange={(e) => setProvider(e.target.value as ProviderValue)}
          >
            {PROVIDER_OPTIONS.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
          <label className="mt-2 block text-xs text-term-muted" htmlFor="ps-model">
            Model
          </label>
          <input
            id="ps-model"
            className="term-input w-full"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder="gemini-3.7-flash"
            spellCheck={false}
          />
          <label className="mt-2 block text-xs text-term-muted" htmlFor="ps-key">
            API key
          </label>
          <input
            id="ps-key"
            className="term-input w-full"
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder="paste key — sent once, stored encrypted"
            autoComplete="off"
          />
          <div className="mt-3 flex gap-2">
            <button className="term-btn" disabled={!apiKey || save.isPending} onClick={() => save.mutate()}>
              {save.isPending ? 'SAVING…' : 'SAVE KEY'}
            </button>
            <button className="term-btn-ghost" disabled={test.isPending} onClick={() => test.mutate()}>
              {test.isPending ? 'TESTING…' : '⟳ HEALTH TEST'}
            </button>
          </div>
          {save.isSuccess && <p className="mt-2 text-xs text-term-green">Key stored (server confirms receipt only).</p>}
          {save.isError && <p className="mt-2 text-xs text-term-red">Save failed — backend unreachable or rejected.</p>}
          {keyStatus.data && keyStatus.data.length > 0 && (
            <ul className="mt-2 space-y-1 text-xs" aria-label="Configured providers">
              {keyStatus.data.map((k) => (
                <li key={k.provider} className="flex justify-between border-b border-term-border pb-1">
                  <span className="text-term-muted">{k.provider}</span>
                  <span className={k.configured ? 'text-term-green' : 'text-term-muted'}>
                    {k.configured ? `configured${k.model ? ` · ${k.model}` : ''}` : 'no key'}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {test.data && (
            <p className={`mt-2 text-xs ${test.data.ok ? 'text-term-green' : 'text-term-red'}`}>
              Test: {test.data.ok ? 'OK' : 'FAIL'}{' '}
              {test.data.latency_ms !== undefined ? `· ${test.data.latency_ms}ms` : ''}{' '}
              {test.data.message ?? ''}
            </p>
          )}
          {test.isError && (
            <p className="mt-2 text-xs text-term-red">
              Health test failed to run ({test.error instanceof Error ? test.error.message : 'backend unreachable'}).
            </p>
          )}
        </section>

        <section className="term-panel p-4">
          <p className="term-label">Task profiles (AI weight capped, fixed for v1)</p>
          <div className="mt-2 space-y-1">
            {AI_PROFILES.map((p) => (
              <label key={p} className="flex cursor-pointer items-center gap-2 text-sm">
                <input type="radio" name="profile" checked={profile === p} onChange={() => pickProfile(p)} />
                {p}
                <span className="text-[10px] text-term-muted">· {PROFILE_HINTS[p]}</span>
              </label>
            ))}
          </div>
          <label className="mt-4 block text-xs text-term-muted" htmlFor="ps-budget">
            Monthly budget cap (USD) · {provider}
          </label>
          <div className="mt-1 flex gap-2">
            <input
              id="ps-budget"
              className="term-input w-32"
              inputMode="decimal"
              value={budget}
              onChange={(e) => setBudget(e.target.value)}
              placeholder="25"
            />
            <button
              className="term-btn-ghost"
              disabled={saveBudget.isPending}
              onClick={() => saveBudget.mutate()}
            >
              {saveBudget.isPending ? 'SAVING…' : 'SAVE BUDGET'}
            </button>
          </div>
          {saveBudget.isSuccess && <p className="mt-1 text-xs text-term-green">Budget saved.</p>}
          {savedBudgets.data && savedBudgets.data[provider] !== undefined && (
            <p className="mt-1 text-xs text-term-muted">
              Saved cap for {provider}: ${savedBudgets.data[provider]} USD/mo.
            </p>
          )}
          {saveBudget.isError && (
            <p className="mt-1 text-xs text-term-amber">
              Budget endpoint not confirmed by backend — not saved; value retained in
              this field only ({budget} USD).
            </p>
          )}
          <p className="term-label mt-4">Provider health</p>
          {health.isLoading && (
            <div className="mt-2">
              <Loading label="checking providers…" />
            </div>
          )}
          {health.data?.providers?.length ? (
            <ul className="mt-2 space-y-1 text-xs">
              {health.data.providers.map((pr) => (
                <li key={pr.name} className="flex justify-between border-b border-term-border pb-1">
                  <span>{pr.name}</span>
                  <span className={pr.status === 'ok' ? 'text-term-green' : 'text-term-red'}>
                    {pr.status}
                    {pr.latency_ms ? ` · ${pr.latency_ms}ms` : ''}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-2 text-xs text-term-muted">
              {health.isError ? 'Health endpoint unreachable — keys can still be saved.' : 'No provider data yet.'}
            </p>
          )}
        </section>
      </div>

      <section className="term-panel p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="term-label">Historical provider performance · by exchange + horizon</p>
          <button
            className="term-btn-ghost text-xs"
            disabled={perf.isFetching}
            onClick={() => perf.refetch()}
          >
            {perf.isFetching ? 'REFRESHING…' : '⟳ REFRESH'}
          </button>
        </div>
        {perf.isLoading && (
          <div className="mt-2">
            <Loading label="loading provider performance…" />
          </div>
        )}
        {perf.isError && (
          <p className="mt-2 text-xs text-term-amber">
            ⚠ Performance endpoint unreachable (
            {perf.error instanceof Error ? perf.error.message : 'unknown error'}) — table
            unavailable, settings above unaffected.
          </p>
        )}
        {perf.data && perf.data.length === 0 && (
          <p className="mt-2 text-xs text-term-muted">
            No historical scores yet — performance accrues as bounded AI opinions resolve
            out-of-sample.
          </p>
        )}
        {perf.data && perf.data.length > 0 && (
          <div className="mt-2 overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-term-muted">
                  <th className="py-1 pr-2">Provider</th>
                  <th className="py-1 pr-2">Model</th>
                  <th className="py-1 pr-2">Exch</th>
                  <th className="py-1 pr-2">Hor</th>
                  <th className="py-1 pr-2">n</th>
                  <th className="py-1 pr-2">Brier</th>
                  <th className="py-1 pr-2">ECE</th>
                  <th className="py-1 pr-2">Hit rate</th>
                </tr>
              </thead>
              <tbody>
                {perf.data.map((row, i) => (
                  <tr key={`${row.provider}-${row.model}-${row.exchange}-${row.horizon_days}-${i}`} className="border-t border-term-border">
                    <td className="py-1 pr-2">{row.provider}</td>
                    <td className="py-1 pr-2 text-term-muted">{row.model || '—'}</td>
                    <td className="py-1 pr-2">{row.exchange || '—'}</td>
                    <td className="py-1 pr-2">{row.horizon_days !== undefined ? `${row.horizon_days}d` : '—'}</td>
                    <td className="py-1 pr-2">{row.n_calls}</td>
                    <td className={`py-1 pr-2 ${row.brier !== null && row.brier !== undefined && row.brier > 0.25 ? 'text-term-red' : 'text-term-green'}`}>
                      {row.brier === null || row.brier === undefined ? '—' : row.brier.toFixed(4)}
                    </td>
                    <td className="py-1 pr-2">
                      {row.ece === null || row.ece === undefined ? '—' : row.ece.toFixed(4)}
                    </td>
                    <td className="py-1 pr-2">
                      {row.hit_rate === null || row.hit_rate === undefined ? '—' : `${(row.hit_rate * 100).toFixed(1)}%`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-1 text-[10px] text-term-muted">
              Brier in red = worse than coin-flip (0.25). Scores are out-of-sample only.
              Brier/ECE stay — until per-model scoring is served; hit rate + call counts are live.
            </p>
          </div>
        )}
        {perf.dataUpdatedAt > 0 && (
          <p className="mt-2 text-[10px] text-term-muted">
            refreshed {new Date(perf.dataUpdatedAt).toLocaleString()} · the performance
            endpoint ships no provenance envelope, so no source/grade badge is shown.
          </p>
        )}
      </section>
    </div>
  );
}
