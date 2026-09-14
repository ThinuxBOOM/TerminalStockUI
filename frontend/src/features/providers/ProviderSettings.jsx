import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AI_PROFILES, api, getAIPerformance, getHealth, getProviderBudgets, getProviderKeysStatus, testProviderHealth } from "../../api/client";
import Loading from "../../components/Loading";
const PROVIDER_OPTIONS = [
  { value: "gemini", label: "gemini" },
  { value: "openai", label: "openai" },
  { value: "anthropic", label: "claude" },
  { value: "xai", label: "grok" }
];
const PROFILE_HINTS = {
  "Quick Insight": "default: Gemini 3.7 Flash \xB7 1\u20132 sentence take",
  "Deep Research": "multi-source structured brief \xB7 higher budget",
  "Forecast Assist": "bounded 21d opinion \xB7 capped 20% influence",
  Report: "scheduled / on-demand full report"
};
const PROFILE_DEFAULT_MODEL = {
  "Quick Insight": "gemini-3.7-flash",
  "Deep Research": "gemini-3.7-flash",
  "Forecast Assist": "gemini-3.7-flash",
  Report: "gemini-3.7-flash"
};
const MAX_PERF_ROWS = 100;
function ProviderSettings() {
  const [provider, setProvider] = useState("gemini");
  const [model, setModel] = useState("gemini-3.7-flash");
  const [apiKey, setApiKey] = useState("");
  const [profile, setProfile] = useState("Quick Insight");
  const [budget, setBudget] = useState("25");
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth, retry: false });
  const keyStatus = useQuery({
    queryKey: ["provider-keys-status"],
    queryFn: getProviderKeysStatus,
    retry: false,
    staleTime: 3e4
  });
  const savedBudgets = useQuery({
    queryKey: ["provider-budgets"],
    queryFn: getProviderBudgets,
    retry: false,
    staleTime: 6e4
  });
  const perf = useQuery({
    queryKey: ["ai-performance"],
    queryFn: getAIPerformance,
    retry: false,
    staleTime: 6e4
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
    }
  });
  const test = useMutation({
    // AI key check: POST /api/ai/providers/health/test (configured vs stub).
    mutationFn: () => testProviderHealth(provider)
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
    }
  });
  function pickProfile(p) {
    setProfile(p);
    setModel(PROFILE_DEFAULT_MODEL[p]);
  }
  return /* @__PURE__ */ React.createElement("div", { className: "space-y-4" }, /* @__PURE__ */ React.createElement("div", { className: "grid gap-4 md:grid-cols-2" }, /* @__PURE__ */ React.createElement("section", { className: "term-panel p-4" }, /* @__PURE__ */ React.createElement("p", { className: "term-label" }, "Provider keys (encrypted at rest, never exposed)"), /* @__PURE__ */ React.createElement("label", { className: "mt-2 block text-xs text-term-muted", htmlFor: "ps-provider" }, "Provider"), /* @__PURE__ */ React.createElement(
    "select",
    {
      id: "ps-provider",
      className: "term-input w-full",
      value: provider,
      onChange: (e) => setProvider(e.target.value)
    },
    PROVIDER_OPTIONS.map((p) => /* @__PURE__ */ React.createElement("option", { key: p.value, value: p.value }, p.label))
  ), /* @__PURE__ */ React.createElement("label", { className: "mt-2 block text-xs text-term-muted", htmlFor: "ps-model" }, "Model"), /* @__PURE__ */ React.createElement(
    "input",
    {
      id: "ps-model",
      className: "term-input w-full",
      value: model,
      onChange: (e) => setModel(e.target.value),
      placeholder: "gemini-3.7-flash",
      spellCheck: false
    }
  ), /* @__PURE__ */ React.createElement("label", { className: "mt-2 block text-xs text-term-muted", htmlFor: "ps-key" }, "API key"), /* @__PURE__ */ React.createElement(
    "input",
    {
      id: "ps-key",
      className: "term-input w-full",
      type: "password",
      value: apiKey,
      onChange: (e) => setApiKey(e.target.value),
      placeholder: "paste key \u2014 sent once, stored encrypted",
      autoComplete: "off"
    }
  ), /* @__PURE__ */ React.createElement("div", { className: "mt-3 flex gap-2" }, /* @__PURE__ */ React.createElement("button", { className: "term-btn", type: "button", disabled: !apiKey || save.isPending, onClick: () => save.mutate() }, save.isPending ? "SAVING\u2026" : "SAVE KEY"), /* @__PURE__ */ React.createElement("button", { className: "term-btn-ghost", type: "button", disabled: test.isPending, onClick: () => test.mutate() }, test.isPending ? "TESTING\u2026" : "\u27F3 HEALTH TEST")), save.isSuccess && /* @__PURE__ */ React.createElement("p", { className: "mt-2 text-xs text-term-green" }, "Key stored (server confirms receipt only)."), save.isError && /* @__PURE__ */ React.createElement("p", { className: "mt-2 text-xs text-term-red", role: "alert" }, "Save failed \u2014 backend unreachable or rejected."), keyStatus.isLoading && /* @__PURE__ */ React.createElement("p", { className: "mt-2 text-xs text-term-muted", role: "status" }, "loading key status\u2026"), keyStatus.isError && /* @__PURE__ */ React.createElement("p", { className: "mt-2 text-xs text-term-amber", role: "status" }, "\u26A0 key status unavailable (", keyStatus.error instanceof Error ? keyStatus.error.message : "backend unreachable", ") \u2014 keys can still be saved."), !keyStatus.isLoading && !keyStatus.isError && (!keyStatus.data || keyStatus.data.length === 0) && /* @__PURE__ */ React.createElement("p", { className: "mt-2 text-xs text-term-muted", role: "status" }, "No provider keys configured yet."), keyStatus.data && keyStatus.data.length > 0 && /* @__PURE__ */ React.createElement("ul", { className: "mt-2 space-y-1 text-xs", "aria-label": "Configured providers" }, keyStatus.data.map((k) => /* @__PURE__ */ React.createElement("li", { key: k.provider, className: "flex justify-between border-b border-term-border pb-1" }, /* @__PURE__ */ React.createElement("span", { className: "text-term-muted" }, k.provider), /* @__PURE__ */ React.createElement("span", { className: k.configured ? "text-term-green" : "text-term-muted" }, k.configured ? `configured${k.model ? ` \xB7 ${k.model}` : ""}` : "no key")))), test.data && /* @__PURE__ */ React.createElement("p", { className: `mt-2 text-xs ${test.data.ok ? "text-term-green" : "text-term-red"}` }, "Test: ", test.data.ok ? "OK" : "FAIL", " ", test.data.latency_ms !== void 0 ? `\xB7 ${test.data.latency_ms}ms` : "", " ", test.data.message ?? ""), test.isError && /* @__PURE__ */ React.createElement("p", { className: "mt-2 text-xs text-term-red" }, "Health test failed to run (", test.error instanceof Error ? test.error.message : "backend unreachable", ").")), /* @__PURE__ */ React.createElement("section", { className: "term-panel p-4" }, /* @__PURE__ */ React.createElement("p", { className: "term-label" }, "Task profiles (AI weight capped, fixed for v1)"), /* @__PURE__ */ React.createElement("div", { className: "mt-2 space-y-1" }, AI_PROFILES.map((p) => /* @__PURE__ */ React.createElement("label", { key: p, className: "flex cursor-pointer items-center gap-2 text-sm" }, /* @__PURE__ */ React.createElement("input", { type: "radio", name: "profile", checked: profile === p, onChange: () => pickProfile(p) }), p, /* @__PURE__ */ React.createElement("span", { className: "text-[10px] text-term-muted" }, "\xB7 ", PROFILE_HINTS[p])))), /* @__PURE__ */ React.createElement("label", { className: "mt-4 block text-xs text-term-muted", htmlFor: "ps-budget" }, "Monthly budget cap (USD) \xB7 ", provider), /* @__PURE__ */ React.createElement("div", { className: "mt-1 flex gap-2" }, /* @__PURE__ */ React.createElement(
    "input",
    {
      id: "ps-budget",
      className: "term-input w-32",
      inputMode: "decimal",
      value: budget,
      onChange: (e) => setBudget(e.target.value),
      placeholder: "25"
    }
  ), /* @__PURE__ */ React.createElement(
    "button",
    {
      className: "term-btn-ghost",
      type: "button",
      disabled: saveBudget.isPending,
      onClick: () => saveBudget.mutate()
    },
    saveBudget.isPending ? "SAVING\u2026" : "SAVE BUDGET"
  )), savedBudgets.isLoading && /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-xs text-term-muted", role: "status" }, "loading saved budgets\u2026"), savedBudgets.isError && /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-xs text-term-amber", role: "status" }, "\u26A0 saved budgets unavailable (", savedBudgets.error instanceof Error ? savedBudgets.error.message : "backend unreachable", ")."), saveBudget.isSuccess && /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-xs text-term-green" }, "Budget saved."), savedBudgets.data && savedBudgets.data[provider] !== void 0 && /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-xs text-term-muted" }, "Saved cap for ", provider, ": $", savedBudgets.data[provider], " USD/mo."), saveBudget.isError && /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-xs text-term-amber" }, "Budget endpoint not confirmed by backend \u2014 not saved; value retained in this field only (", budget, " USD)."), /* @__PURE__ */ React.createElement("p", { className: "term-label mt-4" }, "Provider health"), health.isLoading && /* @__PURE__ */ React.createElement("div", { className: "mt-2" }, /* @__PURE__ */ React.createElement(Loading, { label: "checking providers\u2026" })), health.data?.providers?.length ? /* @__PURE__ */ React.createElement("ul", { className: "mt-2 space-y-1 text-xs" }, health.data.providers.map((pr) => /* @__PURE__ */ React.createElement("li", { key: pr.name, className: "flex justify-between border-b border-term-border pb-1" }, /* @__PURE__ */ React.createElement("span", null, pr.name), /* @__PURE__ */ React.createElement("span", { className: pr.status === "ok" ? "text-term-green" : "text-term-red" }, pr.status, pr.latency_ms ? ` \xB7 ${pr.latency_ms}ms` : "")))) : /* @__PURE__ */ React.createElement("p", { className: "mt-2 text-xs text-term-muted" }, health.isError ? "Health endpoint unreachable \u2014 keys can still be saved." : "No provider data yet."))), /* @__PURE__ */ React.createElement("section", { className: "term-panel p-4" }, /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center justify-between gap-2" }, /* @__PURE__ */ React.createElement("p", { className: "term-label" }, "Historical provider performance \xB7 by exchange + horizon"), /* @__PURE__ */ React.createElement(
    "button",
    {
      className: "term-btn-ghost text-xs",
      type: "button",
      disabled: perf.isFetching,
      onClick: () => perf.refetch()
    },
    perf.isFetching ? "REFRESHING\u2026" : "\u27F3 REFRESH"
  )), perf.isLoading && /* @__PURE__ */ React.createElement("div", { className: "mt-2" }, /* @__PURE__ */ React.createElement(Loading, { label: "loading provider performance\u2026" })), perf.isError && /* @__PURE__ */ React.createElement("p", { className: "mt-2 text-xs text-term-amber" }, "\u26A0 Performance endpoint unreachable (", perf.error instanceof Error ? perf.error.message : "unknown error", ") \u2014 table unavailable, settings above unaffected."), perf.data && perf.data.length === 0 && /* @__PURE__ */ React.createElement("p", { className: "mt-2 text-xs text-term-muted" }, "No historical scores yet \u2014 performance accrues as bounded AI opinions resolve out-of-sample."), perf.data && perf.data.length > 0 && /* @__PURE__ */ React.createElement("div", { className: "mt-2 overflow-x-auto" }, perf.data.length > MAX_PERF_ROWS && /* @__PURE__ */ React.createElement("p", { className: "mb-1 text-[11px] text-term-muted", role: "status" }, "showing first ", MAX_PERF_ROWS, " of ", perf.data.length, " \u2014 refresh narrows to the latest window."), /* @__PURE__ */ React.createElement("table", { className: "w-full text-xs" }, /* @__PURE__ */ React.createElement("thead", null, /* @__PURE__ */ React.createElement("tr", { className: "text-left text-term-muted" }, /* @__PURE__ */ React.createElement("th", { className: "py-1 pr-2" }, "Provider"), /* @__PURE__ */ React.createElement("th", { className: "py-1 pr-2" }, "Model"), /* @__PURE__ */ React.createElement("th", { className: "py-1 pr-2" }, "Exch"), /* @__PURE__ */ React.createElement("th", { className: "py-1 pr-2" }, "Hor"), /* @__PURE__ */ React.createElement("th", { className: "py-1 pr-2" }, "n"), /* @__PURE__ */ React.createElement("th", { className: "py-1 pr-2" }, "Brier"), /* @__PURE__ */ React.createElement("th", { className: "py-1 pr-2" }, "ECE"), /* @__PURE__ */ React.createElement("th", { className: "py-1 pr-2" }, "Hit rate"))), /* @__PURE__ */ React.createElement("tbody", null, perf.data.slice(0, MAX_PERF_ROWS).map((row, i) => /* @__PURE__ */ React.createElement("tr", { key: `${row.provider}-${row.model}-${row.exchange}-${row.horizon_days}-${i}`, className: "border-t border-term-border" }, /* @__PURE__ */ React.createElement("td", { className: "py-1 pr-2" }, row.provider), /* @__PURE__ */ React.createElement("td", { className: "py-1 pr-2 text-term-muted" }, row.model || "\u2014"), /* @__PURE__ */ React.createElement("td", { className: "py-1 pr-2" }, row.exchange || "\u2014"), /* @__PURE__ */ React.createElement("td", { className: "py-1 pr-2" }, row.horizon_days !== void 0 ? `${row.horizon_days}d` : "\u2014"), /* @__PURE__ */ React.createElement("td", { className: "py-1 pr-2" }, row.n_calls), /* @__PURE__ */ React.createElement("td", { className: `py-1 pr-2 ${row.brier !== null && row.brier !== void 0 && row.brier > 0.25 ? "text-term-red" : "text-term-green"}` }, row.brier === null || row.brier === void 0 ? "\u2014" : row.brier.toFixed(4)), /* @__PURE__ */ React.createElement("td", { className: "py-1 pr-2" }, row.ece === null || row.ece === void 0 ? "\u2014" : row.ece.toFixed(4)), /* @__PURE__ */ React.createElement("td", { className: "py-1 pr-2" }, row.hit_rate === null || row.hit_rate === void 0 ? "\u2014" : `${(row.hit_rate * 100).toFixed(1)}%`))))), /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-[10px] text-term-muted" }, "Brier in red = worse than coin-flip (0.25). Scores are out-of-sample only. Brier/ECE stay \u2014 until per-model scoring is served; hit rate + call counts are live.")), perf.dataUpdatedAt > 0 && /* @__PURE__ */ React.createElement("p", { className: "mt-2 text-[10px] text-term-muted" }, "refreshed ", new Date(perf.dataUpdatedAt).toLocaleString(), " \xB7 the performance endpoint ships no provenance envelope, so no source/grade badge is shown.")));
}
export { ProviderSettings as default };
