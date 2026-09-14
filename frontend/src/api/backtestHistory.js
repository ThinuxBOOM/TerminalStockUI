import { api, coalesceInflight, normalizeSymbolParam } from "./client";
const BACKTEST_RECENT_KEY = "onemarket.backtest.recent.v1";
const MAX_RECENT = 20;
function numOrNull(v) {
  if (v === null || v === void 0 || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}
function strOrUndef(v) {
  return typeof v === "string" && v ? v : void 0;
}
function normalizeRun(raw) {
  if (!raw || typeof raw !== "object") return null;
  const r = raw;
  const runId = strOrUndef(r.run_id ?? r.runId ?? r.id);
  if (!runId) return null;
  const horizonsRaw = Array.isArray(r.horizons) ? r.horizons : [];
  const horizons = horizonsRaw.map((h) => Number(h)).filter((n) => Number.isFinite(n));
  const metricsRaw = r.metrics ?? r.results ?? {};
  const metrics = {};
  if (metricsRaw && typeof metricsRaw === "object" && !Array.isArray(metricsRaw)) {
    for (const [k, v] of Object.entries(metricsRaw)) {
      const m = v ?? {};
      metrics[String(k)] = {
        n_folds: m.n_folds !== void 0 ? numOrNull(m.n_folds ?? m.nFolds) : null,
        n_points: m.n_points !== void 0 ? numOrNull(m.n_points ?? m.nPoints ?? m.n) : numOrNull(m.n_points ?? m.n),
        brier: m.brier !== void 0 ? numOrNull(m.brier ?? m.brier_score) : numOrNull(m.brier ?? m.brier_score),
        ece: m.ece !== void 0 ? numOrNull(m.ece ?? m.calibration_error) : numOrNull(m.ece ?? m.calibration_error)
      };
    }
  }
  const reliabilityRaw = r.reliability ?? r.reliability_table ?? r.table;
  const reliability = Array.isArray(reliabilityRaw) ? reliabilityRaw : void 0;
  const params = r.params && typeof r.params === "object" && !Array.isArray(r.params) ? r.params : void 0;
  return {
    run_id: runId,
    as_of: typeof r.as_of === "string" ? r.as_of : null,
    horizons,
    params,
    metrics,
    model_version: typeof r.model_version === "string" ? r.model_version : null,
    feature_version: typeof r.feature_version === "string" ? r.feature_version : null,
    data_version: typeof r.data_version === "string" ? r.data_version : null,
    ...reliability ? { reliability } : {}
  };
}
async function getBacktestHistory(symbol, includeReliability = true) {
  const sym = normalizeSymbolParam(symbol);
  if (!sym) return [];
  return coalesceInflight(`backtest-history:${sym}:${includeReliability}`, async () => {
    try {
      const { data } = await api.get(`/api/backtest/${encodeURIComponent(sym)}`, {
        params: { include_reliability: includeReliability }
      });
      const raw = data ?? {};
      const listRaw = Array.isArray(data) ? data : Array.isArray(raw.runs) ? raw.runs : Array.isArray(raw.results) ? raw.results : Array.isArray(raw.history) ? raw.history : [];
      const out = [];
      for (const row of listRaw) {
        const parsed = normalizeRun(row);
        if (parsed) out.push(parsed);
      }
      return out;
    } catch (err) {
      const status = err?.response?.status;
      if (status === 404 || status === 501) return [];
      throw err;
    }
  });
}
function saveRecentBacktest(symbol, horizons) {
  try {
    if (typeof localStorage === "undefined") return;
    const sym = normalizeSymbolParam(symbol);
    if (!sym) return;
    const clean = horizons.map((h) => Number(h)).filter((n) => Number.isFinite(n));
    const entry = {
      symbol: sym,
      horizons: clean,
      at: (/* @__PURE__ */ new Date()).toISOString()
    };
    const prev = getRecentBacktests();
    const next = [entry, ...prev.filter((r) => r.symbol !== sym)].slice(0, MAX_RECENT);
    localStorage.setItem(BACKTEST_RECENT_KEY, JSON.stringify(next));
  } catch {
  }
}
function getRecentBacktests() {
  try {
    if (typeof localStorage === "undefined") return [];
    const raw = localStorage.getItem(BACKTEST_RECENT_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    const out = [];
    for (const row of parsed) {
      if (!row || typeof row !== "object") continue;
      const r = row;
      const symbol = normalizeSymbolParam(r.symbol);
      if (!symbol) continue;
      const horizons = Array.isArray(r.horizons) ? r.horizons.map((h) => Number(h)).filter((n) => Number.isFinite(n)) : [];
      const at = typeof r.at === "string" ? r.at : (/* @__PURE__ */ new Date(0)).toISOString();
      out.push({ symbol, horizons, at });
    }
    return out.slice(0, MAX_RECENT);
  } catch {
    return [];
  }
}
export { BACKTEST_RECENT_KEY, getBacktestHistory, getRecentBacktests, saveRecentBacktest };
