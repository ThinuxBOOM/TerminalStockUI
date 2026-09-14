import axios from "axios";
import { z } from "zod";
const ProvenanceSchema = z.object({
  source: z.string(),
  as_of: z.string(),
  delay_minutes: z.number(),
  quality_grade: z.string(),
  fallback_used: z.boolean(),
  missing_fields: z.array(z.string())
});
const InstrumentSchema = z.object({
  instrument_id: z.string().optional(),
  symbol: z.string(),
  exchange_mic: z.string().optional(),
  exchange_symbol: z.string().optional(),
  provider_symbol: z.string().optional(),
  company_name: z.string().optional(),
  currency: z.string().optional(),
  country: z.string().optional(),
  sector: z.string().optional()
});
const MARKET_STATES = [
  "open",
  "closed",
  "lunch",
  "delayed",
  "stale"
];
function normalizeMarketState(v) {
  if (typeof v !== "string") return null;
  const s = v.trim().toLowerCase();
  return MARKET_STATES.includes(s) ? s : null;
}
function deriveMarketState(p, explicit) {
  const direct = normalizeMarketState(explicit);
  if (direct) return direct;
  const f = freshnessOf(p);
  if (f === "live") return "open";
  if (f === "stale") return "stale";
  return "delayed";
}
function displaySymbol(r) {
  const prov = (r.provider_symbol ?? "").trim();
  if (prov) return prov;
  const exch = (r.exchange_symbol ?? "").trim();
  if (exch) return exch;
  return r.symbol;
}
const QuoteSchema = z.object({
  symbol: z.string(),
  price: z.number().nullable(),
  change: z.number().optional(),
  change_pct: z.number().optional(),
  currency: z.string().optional(),
  market_state: z.enum(["open", "closed", "lunch", "delayed", "stale"]),
  instrument: InstrumentSchema.passthrough().nullable().optional(),
  ambiguous: z.boolean().optional().default(false),
  candidates: z.array(z.string()).optional().default([]),
  provenance: ProvenanceSchema
  // Backend may nest provenance under `meta.provenance`; normalized in client.
});
const HealthSchema = z.object({
  status: z.string(),
  providers: z.array(
    z.object({
      name: z.string(),
      status: z.string(),
      latency_ms: z.number().optional(),
      last_check: z.string().optional()
    })
  ).optional(),
  provenance: ProvenanceSchema.optional()
});
function resolveBaseUrl() {
  const raw = import.meta.env?.VITE_API_BASE_URL;
  const configured = (raw ?? "").trim().replace(/\/+$/, "");
  if (configured) return configured;
  if (typeof window !== "undefined") {
    const { protocol, hostname } = window.location;
    if (protocol === "https:") return "";
    if (hostname && hostname !== "localhost" && hostname !== "127.0.0.1" && hostname !== "[::1]")
      return "";
  }
  return "http://localhost:8000";
}
const BASE_URL = resolveBaseUrl();
const api = axios.create({
  baseURL: BASE_URL,
  timeout: 15e3,
  headers: { "Content-Type": "application/json" }
});
function normalizeSymbolParam(v) {
  return String(v ?? "").trim().toUpperCase().replace(/\s+/g, "");
}
function httpStatus(err) {
  const e = err;
  const s = e?.response?.status ?? e?.status;
  return typeof s === "number" ? s : null;
}
function isEndpointMissingError(err) {
  const s = httpStatus(err);
  return s === 404 || s === 501;
}
const inflight = /* @__PURE__ */ new Map();
function coalesceInflight(key, fn) {
  const hit = inflight.get(key);
  if (hit) return hit;
  const p = fn().finally(() => {
    if (inflight.get(key) === p) inflight.delete(key);
  });
  inflight.set(key, p);
  return p;
}
function normalizeQuote(raw) {
  const r = raw;
  const prov = r?.provenance ?? r?.meta?.provenance ?? {
    source: "unknown",
    as_of: (/* @__PURE__ */ new Date()).toISOString(),
    delay_minutes: -1,
    quality_grade: "U",
    fallback_used: true,
    missing_fields: ["provenance"]
  };
  const instRaw = r?.instrument ?? null;
  let instrument = null;
  if (instRaw && typeof instRaw === "object") {
    const parsed = InstrumentSchema.passthrough().safeParse({
      ...instRaw,
      symbol: instRaw.symbol ?? instRaw.provider_symbol ?? instRaw.exchange_symbol ?? "UNKNOWN"
    });
    if (parsed.success) instrument = parsed.data;
  }
  const priceRaw = r?.price ?? r?.last;
  const priceNum = priceRaw === null || priceRaw === void 0 || priceRaw === "" ? null : Number(priceRaw);
  const price = priceNum === null || Number.isFinite(priceNum) ? priceNum : null;
  const explicit = normalizeMarketState(
    r?.market_state ?? r?.marketState
  );
  const market_state = explicit ?? deriveMarketState(prov, null);
  const candidatesRaw = r?.candidates;
  return QuoteSchema.parse({
    symbol: r?.symbol ?? r?.ticker ?? instrument?.provider_symbol ?? instrument?.exchange_symbol ?? instrument?.symbol ?? "UNKNOWN",
    price,
    change: r?.change !== void 0 && r?.change !== null ? Number(r.change) : void 0,
    change_pct: r?.change_pct !== void 0 && r?.change_pct !== null ? Number(r.change_pct) : void 0,
    currency: r?.currency ?? instrument?.currency ?? void 0,
    market_state,
    instrument,
    ambiguous: Boolean(r?.ambiguous ?? false),
    candidates: Array.isArray(candidatesRaw) ? candidatesRaw.map((c) => String(c)) : [],
    provenance: prov
  });
}
function normalizeHealthProviders(raw) {
  if (!Array.isArray(raw)) return [];
  return raw.map((row) => {
    const circuit = typeof row.circuit === "string" ? row.circuit : void 0;
    return {
      ...row,
      name: row.name ?? row.provider ?? "unknown",
      status: typeof row.status === "string" ? row.status : circuit === "open" ? "open" : "ok",
      latency_ms: row.latency_ms ?? row.latency_p50_ms
    };
  });
}
async function getHealth() {
  return coalesceInflight("health", async () => {
    const { data } = await api.get("/health");
    const raw = data ?? {};
    if (Array.isArray(raw.providers)) {
      raw.providers = normalizeHealthProviders(raw.providers);
    }
    return HealthSchema.passthrough().parse(data);
  });
}
function normalizeInstrument(raw) {
  const r = raw ?? {};
  const exchange_symbol = r.exchange_symbol ?? r.symbol ?? "";
  const provider_symbol = r.provider_symbol ?? r.providerSymbol ?? "";
  const symbol = r.symbol || provider_symbol || exchange_symbol || "UNKNOWN";
  return InstrumentSchema.passthrough().parse({
    ...r,
    symbol,
    exchange_symbol: exchange_symbol || symbol,
    provider_symbol: provider_symbol || symbol
  });
}
async function searchInstruments(query, market, opts) {
  const q = String(query ?? "").trim();
  if (!q) return [];
  const mic = String(market ?? "").trim().toUpperCase();
  const signal = opts?.signal;
  // Backend /api/instruments/search supports limit (1-50, default 10) +
  // offset (0-200). Clamp here so slider/page callers can't 422 the query.
  const lim = Math.min(50, Math.max(1, Number(opts?.limit ?? 20) || 20));
  const off = Math.min(200, Math.max(0, Number(opts?.offset ?? 0) || 0));
  const params = mic && mic !== "ALL" ? { q, market: mic, limit: lim, offset: off } : { q, limit: lim, offset: off };
  return coalesceInflight(`search:${q.toLowerCase()}:${mic || "ALL"}:${lim}:${off}`, async () => {
    const { data } = await api.get("/api/instruments/search", { params, ...signal ? { signal } : {} });
    const list = Array.isArray(data) ? data : data?.results ?? data?.items ?? [];
    const out = [];
    for (const row of list) {
      try {
        out.push(normalizeInstrument(row));
      } catch {
        continue;
      }
    }
    return out;
  });
}
async function getQuote(symbol, market, opts) {
  const sym = normalizeSymbolParam(symbol);
  const mic = String(market ?? "").trim().toUpperCase();
  const signal = opts?.signal;
  const params = { symbol: sym };
  if (mic && mic !== "ALL") params.market = mic;
  return coalesceInflight(`quote:${sym}:${mic || "ALL"}`, async () => {
    const { data } = await api.get("/api/market_data/quote", { params, ...signal ? { signal } : {} });
    return normalizeQuote(data);
  });
}
function freshnessOf(p) {
  if (p.fallback_used) return "cached";
  if (p.delay_minutes < 0) return "stale";
  let effective = p.delay_minutes;
  const asOfMs = Date.parse(p.as_of);
  if (Number.isFinite(asOfMs)) {
    const ageMin = (Date.now() - asOfMs) / 6e4;
    if (Number.isFinite(ageMin)) effective = Math.max(effective, ageMin);
  }
  if (effective <= 1) return "live";
  if (effective <= 30) return "delayed";
  return "stale";
}
const AI_PROFILES = [
  "Quick Insight",
  "Deep Research",
  "Forecast Assist",
  "Report"
];
const FORECAST_HORIZONS = [5, 21, 63];
function normalizeProvenance(raw, sourceFallback) {
  const r = raw ?? {};
  const nested = r.meta?.provenance;
  const cand = r.provenance ?? nested;
  if (cand && typeof cand === "object") {
    const parsed = ProvenanceSchema.passthrough().safeParse(cand);
    if (parsed.success) return parsed.data;
  }
  return {
    source: r.source ?? sourceFallback,
    as_of: r.as_of ?? (/* @__PURE__ */ new Date()).toISOString(),
    delay_minutes: typeof (r.delay_minutes ?? cand?.delay_minutes) === "number" ? Number(r.delay_minutes ?? cand.delay_minutes) : -1,
    quality_grade: r.quality_grade ?? r.data_quality ?? "U",
    fallback_used: true,
    missing_fields: ["provenance"]
  };
}
function strArray(v) {
  if (Array.isArray(v)) return v.map((x) => String(x));
  if (v === void 0 || v === null) return [];
  return [String(v)];
}
function num(v, fallback = Number.NaN) {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}
const ReliabilityRowSchema = z.object({
  bin_low: z.number(),
  bin_high: z.number(),
  count: z.number(),
  mean_predicted: z.number().nullable().optional(),
  fraction_positive: z.number().nullable().optional()
}).passthrough();
function normalizeReliability(v) {
  if (!Array.isArray(v)) return [];
  const out = [];
  for (const row of v) {
    const parsed = ReliabilityRowSchema.safeParse(row);
    if (parsed.success) out.push(parsed.data);
  }
  return out;
}
const ForecastSchema = z.object({
  symbol: z.string(),
  horizon_days: z.number().optional().default(21),
  label: z.string().optional().default(""),
  probability: z.number().min(0).max(1),
  confidence: z.string().optional().default("Unknown"),
  quality_grade: z.string().optional().default("U"),
  provider: z.string().optional().default("deterministic-engine"),
  why: z.array(z.string()).optional().default([]),
  risks: z.array(z.string()).optional().default([]),
  evidence_ids: z.array(z.string()).optional().default([]),
  inputs: z.record(z.unknown()).optional(),
  versions: z.record(z.unknown()).optional(),
  calibration: z.array(ReliabilityRowSchema).optional().default([]),
  intervals: z.object({ low: z.number(), mid: z.number(), high: z.number() }).passthrough().nullable().optional(),
  limitations: z.array(z.string()).optional().default([]),
  disclosure: z.string().optional(),
  provenance: ProvenanceSchema
}).passthrough();
function normalizeForecast(raw, symbol, horizon) {
  const r = raw ?? {};
  const versions = r.versions ?? {
    ...typeof r.model_name === "string" ? { model_name: r.model_name } : {},
    ...typeof r.model_version === "string" ? { model_version: r.model_version } : {},
    ...typeof r.feature_version === "string" ? { feature_version: r.feature_version } : {},
    ...typeof r.data_version === "string" ? { data_version: r.data_version } : {},
    ...typeof r.as_of === "string" ? { as_of: r.as_of } : {}
  };
  const direction = typeof r.direction === "string" ? r.direction : void 0;
  const label = r.label ?? r.outlook ?? (direction ? `${direction}, ${num(r.horizon_days ?? r.horizon ?? horizon, horizon)}d` : "");
  const intervalsRaw = r.intervals ?? r.expected_return_range ?? r.return_range ?? null;
  const candidate = {
    symbol: r.symbol ?? r.ticker ?? symbol,
    horizon_days: num(r.horizon_days ?? r.horizon ?? horizon, horizon),
    label,
    probability: num(
      r.probability ?? r.direction_probability ?? r.proba ?? r.value,
      Number.NaN
    ),
    confidence: r.confidence ?? r.confidence_level ?? "Unknown",
    quality_grade: r.quality_grade ?? r.data_quality ?? r.grade ?? "U",
    provider: r.provider ?? r.model_name ?? "deterministic-engine",
    why: strArray(r.why ?? r.bull ?? r.bullish_signals ?? r.top_bullish ?? r.catalysts),
    risks: strArray(r.risks ?? r.bear ?? r.bearish_risks ?? r.top_risks),
    evidence_ids: strArray(r.evidence_ids ?? r.evidence ?? []),
    inputs: r.inputs ?? r.features,
    versions: Object.keys(versions).length > 0 ? versions : void 0,
    calibration: normalizeReliability(
      r.calibration ?? r.calibration_history ?? r.reliability ?? r.reliability_table ?? []
    ),
    intervals: intervalsRaw && Number.isFinite(Number(intervalsRaw.low)) && Number.isFinite(Number(intervalsRaw.mid)) && Number.isFinite(Number(intervalsRaw.high)) ? {
      low: Number(intervalsRaw.low),
      mid: Number(intervalsRaw.mid),
      high: Number(intervalsRaw.high)
    } : null,
    limitations: strArray(r.limitations),
    disclosure: r.disclosure,
    provenance: normalizeProvenance(r, "forecast-api")
  };
  return ForecastSchema.parse(candidate);
}
const FORECAST_TIMEOUT_MS = 6e4;
async function getForecast(symbol, horizon = 21, opts) {
  const sym = normalizeSymbolParam(symbol);
  const h = horizon;
  const signal = opts?.signal;
  return coalesceInflight(`forecast:${sym}:${h}`, async () => {
    try {
      const { data } = await api.get(`/api/forecast/${encodeURIComponent(sym)}`, {
        params: { horizon: h },
        timeout: FORECAST_TIMEOUT_MS,
        ...signal ? { signal } : {}
      });
      return normalizeForecast(data, sym, h);
    } catch (pathErr) {
      if (!isEndpointMissingError(pathErr)) throw pathErr;
      try {
        const { data } = await api.get("/api/forecast", {
          params: { symbol: sym, horizon: h },
          timeout: FORECAST_TIMEOUT_MS,
          ...signal ? { signal } : {}
        });
        return normalizeForecast(data, sym, h);
      } catch {
        throw pathErr;
      }
    }
  });
}
const AnalyticsSchema = z.object({
  symbol: z.string(),
  technical: z.record(z.unknown()).optional().default({}),
  fundamentals: z.record(z.unknown()).optional().default({}),
  quality: z.record(z.unknown()).optional().default({}),
  valuation: z.record(z.unknown()).optional().default({}),
  note: z.string().optional(),
  provenance: ProvenanceSchema
}).passthrough();
function normalizeAnalytics(raw, symbol) {
  const r = raw ?? {};
  const nested = r.data ?? r.analytics ?? {};
  const pick = (key) => r[key] ?? nested[key] ?? {};
  const noteRaw = (typeof r.note === "string" ? r.note : void 0) ?? (typeof nested.note === "string" ? nested.note : void 0);
  const candidate = {
    symbol: r.symbol ?? nested.symbol ?? symbol,
    technical: pick("technical"),
    fundamentals: pick("fundamentals"),
    quality: pick("quality"),
    valuation: pick("valuation"),
    ...noteRaw ? { note: noteRaw } : {},
    provenance: normalizeProvenance({ ...nested, ...r }, "analytics-api")
  };
  return AnalyticsSchema.parse(candidate);
}
const ANALYTICS_TIMEOUT_MS = 6e4;
async function getAnalytics(symbol, opts) {
  const sym = normalizeSymbolParam(symbol);
  const signal = opts?.signal;
  return coalesceInflight(`analytics:${sym}`, async () => {
    try {
      const { data } = await api.get(`/api/analytics/${encodeURIComponent(sym)}`, {
        timeout: ANALYTICS_TIMEOUT_MS,
        ...signal ? { signal } : {}
      });
      return normalizeAnalytics(data, sym);
    } catch (pathErr) {
      if (!isEndpointMissingError(pathErr)) throw pathErr;
      try {
        const { data } = await api.get("/api/analytics", {
          params: { symbol: sym },
          timeout: ANALYTICS_TIMEOUT_MS,
          ...signal ? { signal } : {}
        });
        return normalizeAnalytics(data, sym);
      } catch {
        throw pathErr;
      }
    }
  });
}
const BacktestSchema = z.object({
  symbol: z.string(),
  horizons: z.array(z.number()).optional().default([]),
  brier: z.number().nullable().optional(),
  ece: z.number().nullable().optional(),
  reliability: z.array(ReliabilityRowSchema).optional().default([]),
  n_windows: z.number().nullable().optional(),
  failures: z.array(z.string()).optional().default([]),
  notes: z.string().optional(),
  provenance: ProvenanceSchema
}).passthrough();
function normalizeBacktest(raw, symbol, horizons) {
  const r = raw ?? {};
  const brierRaw = r.brier ?? r.brier_score;
  const eceRaw = r.ece ?? r.calibration_error ?? r.ece_score;
  const candidate = {
    symbol: r.symbol ?? symbol,
    horizons: (Array.isArray(r.horizons) ? r.horizons : horizons).map((h) => Number(h)) ?? horizons,
    brier: brierRaw === void 0 || brierRaw === null ? null : num(brierRaw, Number.NaN),
    ece: eceRaw === void 0 || eceRaw === null ? null : num(eceRaw, Number.NaN),
    reliability: normalizeReliability(r.reliability ?? r.reliability_table ?? r.table ?? []),
    n_windows: r.n_windows === void 0 || r.n_windows === null ? r.n === void 0 || r.n === null ? null : num(r.n, Number.NaN) : num(r.n_windows, Number.NaN),
    failures: strArray(r.failures ?? r.errors ?? []),
    notes: r.notes,
    provenance: normalizeProvenance(r, "backtest-api")
  };
  const parsed = BacktestSchema.parse(candidate);
  return {
    ...parsed,
    brier: parsed.brier !== void 0 && Number.isNaN(parsed.brier) ? null : parsed.brier,
    ece: parsed.ece !== void 0 && Number.isNaN(parsed.ece) ? null : parsed.ece
  };
}
const BACKTEST_TIMEOUT_MS = 6e4;
const RANK_TIMEOUT_MS = 6e4;
async function runBacktest(symbol, horizons) {
  const sym = normalizeSymbolParam(symbol);
  const h = Array.isArray(horizons) ? [...horizons] : horizons;
  return coalesceInflight(`backtest:${sym}:${JSON.stringify(h)}`, async () => {
    try {
      const { data } = await api.post(
        "/api/backtest/run",
        { symbol: sym, horizons: h },
        { timeout: BACKTEST_TIMEOUT_MS }
      );
      return normalizeBacktest(flattenBacktestRun(data, sym, h), sym, h);
    } catch (runErr) {
      if (!isEndpointMissingError(runErr)) throw runErr;
      try {
        const { data } = await api.post(
          "/api/backtest",
          { symbol: sym, horizons: h },
          { timeout: BACKTEST_TIMEOUT_MS }
        );
        return normalizeBacktest(flattenBacktestRun(data, sym, h), sym, h);
      } catch {
        throw runErr;
      }
    }
  });
}
function flattenBacktestRun(raw, symbol, horizons) {
  const r = raw ?? {};
  const results = r.results;
  if (results && typeof results === "object" && !Array.isArray(results)) {
    const first = String(horizons[0] ?? Object.keys(results)[0] ?? "");
    const h = results[first] ?? results[String(Number(first))] ?? {};
    const nWindows = h.n_points ?? h.n_folds ?? r.n_windows ?? r.n ?? null;
    return {
      ...r,
      symbol: r.symbol ?? symbol,
      horizons,
      brier: h.brier ?? r.brier ?? r.brier_score ?? null,
      ece: h.ece ?? r.ece ?? r.calibration_error ?? null,
      reliability: h.reliability ?? r.reliability ?? r.reliability_table ?? r.table ?? [],
      n_windows: nWindows,
      failures: r.failures ?? r.errors ?? [],
      notes: r.notes,
      provenance: r.provenance ?? normalizeProvenance(r, "backtest-api")
    };
  }
  return raw;
}
const AIOpinionSchema = z.object({
  direction: z.string(),
  probability: z.number().min(0).max(1),
  time_horizon_days: z.number().int().positive(),
  catalysts: z.array(z.string()).optional().default([]),
  risks: z.array(z.string()).optional().default([]),
  evidence_ids: z.array(z.string()),
  limitations: z.array(z.string()).optional().default([]),
  provider: z.string().optional(),
  model: z.string().optional(),
  disagreement: z.boolean().optional(),
  provenance: ProvenanceSchema.optional()
}).passthrough();
function normalizeAIOpinion(raw) {
  const r = raw ?? {};
  const opinion = r.opinion ?? r.data ?? r;
  const candidate = {
    ...opinion,
    direction: opinion.direction ?? opinion.outlook,
    probability: num(opinion.probability ?? opinion.proba, Number.NaN),
    time_horizon_days: num(
      opinion.time_horizon_days ?? opinion.horizon_days ?? opinion.horizon,
      Number.NaN
    ),
    catalysts: strArray(opinion.catalysts),
    risks: strArray(opinion.risks),
    // evidence_ids intentionally NOT defaulted: missing IDs must fail validation
    // (spec M5: reject claims without evidence IDs).
    limitations: strArray(opinion.limitations)
  };
  return AIOpinionSchema.parse(candidate);
}
const AI_TIMEOUT_MS = 6e4;
async function postAIInsight(symbol, profile) {
  const sym = normalizeSymbolParam(symbol);
  return coalesceInflight(`ai-insight:${sym}:${profile}`, async () => {
    const { data } = await api.post(
      "/api/ai/insight",
      { symbol: sym, profile },
      { timeout: AI_TIMEOUT_MS }
    );
    return normalizeAIOpinion(data);
  });
}
function friendlyAIError(error) {
  const message = error instanceof Error ? error.message : String(error ?? "unknown error");
  if (error?.code === "ECONNABORTED" || /timeout of \d+ms exceeded/i.test(message)) {
    return "AI took longer than 60s (cold start + thinking model) \u2014 deterministic forecast unaffected. Retry; repeat calls are usually instant via the evidence cache.";
  }
  return `AI request failed (${message}).`;
}
const AIPerformanceRowSchema = z.object({
  provider: z.string(),
  model: z.string().optional().default(""),
  exchange: z.string().optional().default(""),
  horizon_days: z.number().optional(),
  n_calls: z.number().optional().default(0),
  brier: z.number().nullable().optional(),
  ece: z.number().nullable().optional(),
  hit_rate: z.number().nullable().optional()
}).passthrough();
function normalizeAIPerformance(raw) {
  const r = raw;
  const list = Array.isArray(r) ? r : r?.rows ?? r?.performance ?? r?.providers ?? [];
  if (!Array.isArray(list)) return [];
  const out = [];
  for (const row of list) {
    const rr = row;
    const candidate = {
      ...rr,
      provider: rr.provider ?? rr.name,
      model: rr.model ?? "",
      horizon_days: rr.horizon_days ?? rr.horizon,
      n_calls: rr.n_calls ?? rr.total_calls ?? rr.calls_1h ?? rr.calls ?? 0,
      brier: rr.brier ?? null,
      ece: rr.ece ?? rr.calibration_error ?? null,
      hit_rate: rr.hit_rate ?? rr.accuracy ?? null
    };
    const parsed = AIPerformanceRowSchema.safeParse(candidate);
    if (parsed.success) out.push(parsed.data);
  }
  return out;
}
async function getAIPerformance() {
  return coalesceInflight("ai-performance", async () => {
    try {
      const { data } = await api.get("/api/ai/providers/performance");
      return normalizeAIPerformance(data);
    } catch (pathErr) {
      if (!isEndpointMissingError(pathErr)) throw pathErr;
      try {
        const { data } = await api.get("/api/ai/performance");
        return normalizeAIPerformance(data);
      } catch {
        throw pathErr;
      }
    }
  });
}
async function getProviderKeysStatus() {
  return coalesceInflight("provider-keys-status", async () => {
    const { data } = await api.get("/api/providers/keys/status");
    const list = data?.providers;
    if (!Array.isArray(list)) return [];
    return list.map((p) => ({
      provider: String(p.provider ?? ""),
      model: typeof p.model === "string" ? p.model : "",
      configured: p.configured === true,
      updated_at: typeof p.updated_at === "string" ? p.updated_at : null
    }));
  });
}
async function getProviderBudgets() {
  return coalesceInflight("provider-budgets", async () => {
    const { data } = await api.get("/api/providers/budget");
    const budgets = data?.budgets;
    if (!budgets || typeof budgets !== "object") return {};
    const out = {};
    for (const [k, v] of Object.entries(budgets)) {
      if (typeof v === "number" && Number.isFinite(v)) out[k] = v;
    }
    return out;
  });
}
function normalizeAIHealthTest(raw, provider) {
  const d = raw ?? {};
  if (typeof d.ok === "boolean")
    return {
      ok: d.ok,
      latency_ms: d.latency_ms,
      message: d.message,
      provider
    };
  const list = Array.isArray(d.providers) ? d.providers : null;
  if (!list || list.length === 0) return null;
  const want = provider.trim().toLowerCase();
  const match = list.find((r) => String(r.provider ?? "").trim().toLowerCase() === want) ?? list[0];
  if (!match || typeof match !== "object") return null;
  const name = typeof match.provider === "string" && match.provider ? match.provider : provider;
  const model = typeof match.model === "string" ? match.model : "";
  if (typeof match.configured === "boolean") {
    const configured = match.configured;
    return {
      ok: configured,
      message: configured ? `configured${model ? ` \xB7 model ${model}` : ""}` : "not configured (stub mode) \u2014 API key missing",
      provider: name
    };
  }
  return null;
}
async function testProviderHealth(provider) {
  const prov = String(provider ?? "").trim();
  try {
    const { data: data2 } = await api.post("/api/ai/providers/health/test", { provider: prov });
    const parsed = normalizeAIHealthTest(data2, prov);
    if (parsed) return parsed;
  } catch (err) {
    if (!isEndpointMissingError(err)) throw err;
  }
  const { data } = await api.post(
    "/api/providers/health/test",
    { provider: prov },
    { params: { provider: prov } }
  );
  const d = data ?? {};
  if (typeof d.ok === "boolean")
    return { ok: d.ok, latency_ms: d.latency_ms, message: d.message, provider: prov };
  const total = Number(d.total_calls ?? 0);
  return {
    ok: d.circuit !== "open",
    latency_ms: d.latency_p50_ms !== void 0 ? Number(d.latency_p50_ms) : void 0,
    message: total > 0 ? `probe recorded (${total} total calls)` : "probe recorded",
    provider: d.provider ?? prov
  };
}
const EURONEXT_MICS = ["XPAR", "XAMS", "XBRU"];
const SUPPORTED_MARKET_MICS = [
  "XNYS",
  "XNAS",
  "XSHG",
  "XPAR",
  "XAMS",
  "XBRU"
];
const TARGET_CURRENCIES = ["USD", "EUR", "CNY"];
function normalizeTargetCcy(v) {
  const s = String(v ?? "").trim().toUpperCase();
  return TARGET_CURRENCIES.includes(s) ? s : "USD";
}
const FX_PROVENANCE_MISSING = "FX_PROVENANCE_MISSING";
function isFxProvenanceMissingError(err) {
  const e = err;
  const payload = e?.response?.data ?? e?.data ?? {};
  const nested = payload.error ?? {};
  const code = nested.code ?? payload.code ?? (typeof e?.code === "string" ? e.code : void 0);
  if (typeof code === "string" && code.toUpperCase().includes(FX_PROVENANCE_MISSING))
    return true;
  const msg = String(
    nested.message ?? payload.message ?? payload.detail ?? e?.message ?? ""
  );
  return msg.toUpperCase().includes(FX_PROVENANCE_MISSING);
}
function isFreshFxProvenance(p) {
  if (!p || typeof p !== "object") return false;
  if (p.fallback_used) return false;
  if (typeof p.delay_minutes !== "number" || p.delay_minutes < 0 || p.delay_minutes > 30)
    return false;
  const grade = String(p.quality_grade ?? "").trim().toUpperCase();
  return grade === "A" || grade === "B";
}
const FXRateSchema = z.object({
  base: z.string(),
  quote: z.string(),
  rate: z.number(),
  provenance: ProvenanceSchema
}).passthrough();
const FXConvertResultSchema = z.object({
  amount: z.number(),
  from: z.string(),
  to: z.string(),
  converted: z.number().nullable(),
  rate: z.number().nullable().optional(),
  provenance: ProvenanceSchema
}).passthrough();
const RankedRowSchema = z.object({
  symbol: z.string(),
  price: z.number().nullable().optional(),
  currency: z.string().optional(),
  converted_price: z.number().nullable().optional(),
  target_ccy: z.string().optional(),
  change_pct: z.number().nullable().optional(),
  market_state: z.string().nullable().optional(),
  instrument: InstrumentSchema.passthrough().nullable().optional(),
  provenance: ProvenanceSchema
}).passthrough();
const RankResponseSchema = z.object({
  target_ccy: z.string(),
  ranking: z.array(RankedRowSchema),
  fx_provenance: ProvenanceSchema.nullable()
}).passthrough();
function ccy(v, fallback) {
  const s = String(v ?? fallback).trim().toUpperCase();
  return /^[A-Z]{3}$/.test(s) ? s : fallback;
}
function rateNumber(v) {
  if (v === null || v === void 0 || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}
function normalizeFXRate(raw, base, quote) {
  const r = raw ?? {};
  const rate = rateNumber(r.rate ?? r.fx_rate ?? r.price ?? r.value) ?? Number.NaN;
  return FXRateSchema.parse({
    ...r,
    base: ccy(r.base ?? r.from ?? base, ccy(base, "EUR")),
    quote: ccy(r.quote ?? r.to ?? r.target_ccy ?? quote, ccy(quote, "USD")),
    rate,
    provenance: normalizeProvenance(r, "fx-api")
  });
}
async function getFXRate(base, quote) {
  const b = ccy(base, "EUR");
  const q = ccy(quote, "USD");
  return coalesceInflight(`fx-rate:${b}:${q}`, async () => {
    const { data } = await api.get("/api/fx/rate", { params: { base: b, quote: q } });
    return normalizeFXRate(data, b, q);
  });
}
function normalizeConvert(raw, amount, from, to) {
  const r = raw ?? {};
  const converted = rateNumber(
    r.converted ?? r.converted_amount ?? r.result ?? r.value ?? r.price
  ) ?? null;
  const rate = rateNumber(r.rate ?? r.fx_rate);
  return FXConvertResultSchema.parse({
    ...r,
    amount: Number.isFinite(Number(r.amount ?? amount)) ? Number(r.amount ?? amount) : amount,
    from: ccy(r.from ?? r.base ?? from, ccy(from, "EUR")),
    to: ccy(r.to ?? r.quote ?? r.target_ccy ?? to, ccy(to, "USD")),
    converted,
    rate,
    provenance: normalizeProvenance(r, "fx-api")
  });
}
async function convertFX(amount, from, to) {
  const f = ccy(from, "EUR");
  const t = ccy(to, "USD");
  const amt = Number(amount);
  return coalesceInflight(`fx-convert:${amt}:${f}:${t}`, async () => {
    const { data } = await api.post("/api/fx/convert", {
      amount,
      from: f,
      to: t
    });
    return normalizeConvert(data, amount, f, t);
  });
}
function normalizeRankedRow(raw, targetCcy) {
  const r = raw ?? {};
  const instRaw = r.instrument ?? null;
  let instrument = null;
  if (instRaw && typeof instRaw === "object") {
    const parsed = InstrumentSchema.passthrough().safeParse({
      ...instRaw,
      symbol: instRaw.symbol ?? instRaw.provider_symbol ?? instRaw.exchange_symbol ?? r.symbol ?? "UNKNOWN"
    });
    if (parsed.success) instrument = parsed.data;
  }
  const price = rateNumber(r.price ?? r.last);
  const converted = rateNumber(
    r.converted_price ?? r.converted ?? r.price_in_target ?? r.target_price
  );
  const changePct = rateNumber(r.change_pct ?? r.changePct);
  return RankedRowSchema.parse({
    ...r,
    symbol: r.symbol ?? r.ticker ?? instrument?.provider_symbol ?? instrument?.symbol ?? "UNKNOWN",
    price,
    currency: r.currency ?? instrument?.currency ?? void 0,
    converted_price: converted,
    target_ccy: ccy(r.target_ccy ?? r.targetCcy ?? targetCcy, ccy(targetCcy, "USD")),
    change_pct: changePct,
    market_state: r.market_state ?? r.marketState ?? void 0,
    instrument,
    provenance: normalizeProvenance(r, "market-data-api")
  });
}
function normalizeRank(raw, symbols, targetCcy) {
  const r = raw ?? {};
  const listRaw = Array.isArray(r.ranked) ? r.ranked : Array.isArray(r.ranking) ? r.ranking : Array.isArray(r.results) ? r.results : Array.isArray(r.items) ? r.items : Array.isArray(r.rows) ? r.rows : [];
  const ranking = [];
  for (const row of listRaw) {
    try {
      ranking.push(normalizeRankedRow(row, targetCcy));
    } catch {
      continue;
    }
  }
  const fxRaw = r.fx_provenance ?? r.fxProvenance ?? r.fx?.provenance ?? r.provenance ?? null;
  let fx_provenance = null;
  if (fxRaw && typeof fxRaw === "object") {
    const parsed2 = ProvenanceSchema.passthrough().safeParse(fxRaw);
    if (parsed2.success) fx_provenance = parsed2.data;
  }
  const target = ccy(
    r.target_ccy ?? r.targetCcy ?? r.to ?? targetCcy,
    ccy(targetCcy, "USD")
  );
  const parsed = RankResponseSchema.parse({
    ...r,
    target_ccy: target,
    ranking,
    fx_provenance
  });
  return { ...parsed, symbols: [...symbols] };
}
async function rankCrossMarket(symbols, targetCcy, opts) {
  const target = ccy(targetCcy, "USD");
  const seen = /* @__PURE__ */ new Set();
  const clean = [];
  for (const s of symbols ?? []) {
    const norm = normalizeSymbolParam(s);
    if (!norm || seen.has(norm)) continue;
    seen.add(norm);
    clean.push(norm);
  }
  const signal = opts?.signal;
  return coalesceInflight(`fx-rank:${clean.join(",")}:${target}`, async () => {
    const { data } = await api.post(
      "/api/fx/rank",
      {
        symbols: clean,
        target_ccy: target
      },
      { timeout: RANK_TIMEOUT_MS, ...signal ? { signal } : {} }
    );
    return normalizeRank(data, clean, target);
  });
}
function numOrUndef(v) {
  if (v === void 0 || v === null || v === "") return void 0;
  const n = Number(v);
  return Number.isFinite(n) ? n : void 0;
}
async function getProvidersHealth() {
  return coalesceInflight("providers-health", async () => {
    const { data } = await api.get("/api/providers/health");
    const raw = data ?? {};
    const list = Array.isArray(data) ? data : Array.isArray(raw.providers) ? raw.providers : [];
    return list.map((row) => {
      const name = String(
        row.provider ?? row.name ?? row.id ?? "unknown"
      );
      const circuit = typeof row.circuit === "string" ? row.circuit : void 0;
      const latencyP50 = numOrUndef(row.latency_p50_ms ?? row.latency_ms);
      const status = typeof row.status === "string" ? row.status : circuit === "open" ? "open" : "ok";
      return {
        name,
        status,
        latency_ms: latencyP50 ?? numOrUndef(row.latency_p95_ms),
        latency_p50_ms: numOrUndef(row.latency_p50_ms),
        latency_p95_ms: numOrUndef(row.latency_p95_ms),
        error_rate_1h: numOrUndef(row.error_rate_1h),
        calls_1h: numOrUndef(row.calls_1h),
        total_calls: numOrUndef(row.total_calls),
        circuit,
        last_check: typeof row.last_check === "string" ? row.last_check : void 0
      };
    });
  });
}
async function getAuditForecasts(limit = 5) {
  const n = Number.isFinite(Number(limit)) ? Math.min(200, Math.max(1, Number(limit))) : 5;
  return coalesceInflight(`audit-forecasts:${n}`, async () => {
    const { data } = await api.get("/api/audit/forecasts", { params: { limit: n } });
    const raw = data ?? {};
    const listRaw = Array.isArray(data) ? data : Array.isArray(raw.forecasts) ? raw.forecasts : Array.isArray(raw.results) ? raw.results : [];
    const forecasts = listRaw.map((f) => ({
      ...f,
      forecast_id: typeof f.forecast_id === "string" ? f.forecast_id : void 0,
      symbol: typeof f.symbol === "string" ? f.symbol : void 0,
      horizon_days: f.horizon_days === void 0 || f.horizon_days === null ? void 0 : Number(f.horizon_days),
      direction_probability: f.direction_probability === void 0 || f.direction_probability === null ? null : numOrUndef(f.direction_probability) ?? null,
      confidence: typeof f.confidence === "string" ? f.confidence : void 0,
      model_version: typeof f.model_version === "string" ? f.model_version : void 0,
      feature_version: typeof f.feature_version === "string" ? f.feature_version : void 0,
      data_version: typeof f.data_version === "string" ? f.data_version : void 0,
      target_date: typeof f.target_date === "string" ? f.target_date : void 0,
      created_at: typeof f.created_at === "string" ? f.created_at : void 0
    }));
    return {
      forecasts,
      count: typeof raw.count === "number" ? raw.count : forecasts.length,
      disclosure: typeof raw.disclosure === "string" ? raw.disclosure : "Not investment advice. For informational purposes only."
    };
  });
}
const ScreenerRowSchema = z.object({
  symbol: z.string(),
  company_name: z.string().optional().default(""),
  exchange_mic: z.string().optional().default(""),
  currency: z.string().optional().default("USD"),
  price: z.number().nullable().optional(),
  change_pct: z.number().nullable().optional(),
  market_state: z.string().nullable().optional(),
  direction_probability: z.number().min(0).max(1),
  confidence: z.string().optional().default("Unknown"),
  model_version: z.string().optional().default(""),
  quality: z.record(z.unknown()).optional(),
  horizon: z.number().optional(),
  horizons: z.array(z.number()).optional().default([]),
  provenance: ProvenanceSchema
}).passthrough();
const ScreenerSkippedSchema = z.object({
  symbol: z.string(),
  reason: z.string().optional().default("")
}).passthrough();
const ScreenerResponseSchema = z.object({
  results: z.array(ScreenerRowSchema).optional().default([]),
  count: z.number().optional().default(0),
  universe_size: z.number().optional().default(0),
  filtered_total: z.number().optional().default(0),
  offset: z.number().optional().default(0),
  skipped: z.array(ScreenerSkippedSchema).optional().default([]),
  horizon: z.number().optional(),
  disclosure: z.string().optional().default("")
}).passthrough();
function normalizeScreenerRow(raw, horizon) {
  const r = raw ?? {};
  const prob = num(
    r.direction_probability ?? r.probability ?? r.direction_prob ?? r.proba,
    Number.NaN
  );
  const candidate = {
    ...r,
    symbol: r.symbol ?? r.ticker ?? "UNKNOWN",
    company_name: r.company_name ?? r.companyName ?? r.name ?? "",
    exchange_mic: r.exchange_mic ?? r.exchangeMic ?? r.mic ?? "",
    currency: r.currency ?? "USD",
    price: rateNumber(r.price ?? r.last),
    change_pct: rateNumber(r.change_pct ?? r.changePct),
    market_state: r.market_state ?? r.marketState ?? null,
    direction_probability: prob,
    confidence: r.confidence ?? "Unknown",
    model_version: r.model_version ?? "",
    horizon: num(r.horizon ?? horizon, horizon),
    horizons: Array.isArray(r.horizons) ? r.horizons.map((h) => Number(h)).filter((h) => Number.isFinite(h)) : [horizon],
    provenance: normalizeProvenance(r, "screener-api")
  };
  return ScreenerRowSchema.parse(candidate);
}
function normalizeScreener(raw, horizon) {
  const r = raw ?? {};
  const listRaw = Array.isArray(r.results) ? r.results : Array.isArray(r.rows) ? r.rows : Array.isArray(r.items) ? r.items : [];
  const results = [];
  for (const row of listRaw) {
    try {
      results.push(normalizeScreenerRow(row, horizon));
    } catch {
      continue;
    }
  }
  const skippedRaw = Array.isArray(r.skipped) ? r.skipped : [];
  const skipped = skippedRaw.flatMap((s) => {
    const parsed = ScreenerSkippedSchema.safeParse(s);
    return parsed.success ? [parsed.data] : [];
  });
  return ScreenerResponseSchema.parse({
    ...r,
    results,
    count: typeof r.count === "number" ? r.count : results.length,
    universe_size: typeof r.universe_size === "number" ? r.universe_size : typeof r.universeSize === "number" ? r.universeSize : results.length,
    filtered_total: typeof r.filtered_total === "number" ? r.filtered_total : typeof r.filteredTotal === "number" ? r.filteredTotal : results.length,
    offset: typeof r.offset === "number" ? r.offset : 0,
    skipped,
    horizon: num(r.horizon ?? horizon, horizon),
    disclosure: r.disclosure ?? ""
  });
}
const SCREENER_TIMEOUT_MS = 6e4;
async function getScreener(params = {}, opts = {}) {
  const horizon = params.horizon ?? 21;
  const mic = String(params.market ?? "").trim().toUpperCase();
  const minDir = params.minDirection ?? 0.5;
  const lim = Math.min(50, Math.max(1, Number(params.limit ?? 20) || 20));
  const off = Math.min(200, Math.max(0, Number(params.offset ?? 0) || 0));
  const signal = opts?.signal ?? params.signal;
  const query = {
    horizon,
    min_direction: minDir,
    limit: lim,
    offset: off
  };
  if (mic && mic !== "ALL") query.market = mic;
  return coalesceInflight(`screener:${mic || "ALL"}:${horizon}:${minDir}:${lim}:${off}`, async () => {
    const { data } = await api.get("/api/screener", {
      params: query,
      timeout: SCREENER_TIMEOUT_MS,
      ...signal ? { signal } : {}
    });
    return normalizeScreener(data, horizon);
  });
}
const BarSchema = z.object({
  ts: z.string(),
  open: z.number().nullable().optional(),
  high: z.number().nullable().optional(),
  low: z.number().nullable().optional(),
  close: z.number().nullable().optional(),
  volume: z.number().nullable().optional()
}).passthrough();
const BarsResponseSchema = z.object({
  symbol: z.string(),
  instrument_id: z.string().nullable().optional(),
  timeframe: z.string().optional().default("1d"),
  bars: z.array(BarSchema).optional().default([]),
  provenance: ProvenanceSchema
}).passthrough();
function numFinite(v) {
  if (v === null || v === void 0 || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}
function normalizeBarTime(v) {
  if (typeof v === "number" && Number.isFinite(v)) {
    const ms = Math.abs(v) < 1e12 ? v * 1e3 : v;
    const d = new Date(ms);
    return Number.isNaN(d.getTime()) ? null : d.toISOString().slice(0, 10);
  }
  if (typeof v === "string") {
    const s = v.trim();
    if (!s) return null;
    const day = s.slice(0, 10);
    if (/^\d{4}-\d{2}-\d{2}$/.test(day)) return day;
    const d = new Date(s);
    return Number.isNaN(d.getTime()) ? null : d.toISOString().slice(0, 10);
  }
  return null;
}
function normalizeBarsToCandles(raw, symbol, timeframe = "1d") {
  const r = raw ?? {};
  const listRaw = Array.isArray(r.bars) ? r.bars : Array.isArray(r.data) ? r.data : Array.isArray(r.candles) ? r.candles : [];
  const candles = [];
  for (const row of listRaw) {
    if (!row || typeof row !== "object") continue;
    const rec = row;
    const time = normalizeBarTime(rec.ts ?? rec.time ?? rec.date);
    if (!time) continue;
    const open = numFinite(rec.open);
    const high = numFinite(rec.high);
    const low = numFinite(rec.low);
    const close = numFinite(rec.close);
    if (open === null || high === null || low === null || close === null) continue;
    candles.push({ time, open, high, low, close });
  }
  return {
    symbol: r.symbol ?? r.ticker ?? symbol,
    timeframe: r.timeframe ?? timeframe,
    candles,
    provenance: normalizeProvenance(r, "bars-api")
  };
}
async function getBars(symbol, timeframe = "1d", limit = 90, opts) {
  const sym = normalizeSymbolParam(symbol);
  const tf = String(timeframe ?? "1d").trim() || "1d";
  const n = Number.isFinite(Number(limit)) ? Math.min(250, Math.max(1, Math.floor(Number(limit)))) : 90;
  const signal = opts?.signal;
  return coalesceInflight(`bars:${sym}:${tf}:${n}`, async () => {
    const { data } = await api.get("/api/market_data/bars", {
      params: { symbol: sym, timeframe: tf, limit: n },
      ...signal ? { signal } : {}
    });
    return normalizeBarsToCandles(data, sym, tf);
  });
}
export { AIOpinionSchema, AIPerformanceRowSchema, AI_PROFILES, AI_TIMEOUT_MS, ANALYTICS_TIMEOUT_MS, AnalyticsSchema, BACKTEST_TIMEOUT_MS, BacktestSchema, BarSchema, BarsResponseSchema, EURONEXT_MICS, FORECAST_HORIZONS, FORECAST_TIMEOUT_MS, FXConvertResultSchema, FXRateSchema, FX_PROVENANCE_MISSING, ForecastSchema, HealthSchema, InstrumentSchema, MARKET_STATES, ProvenanceSchema, QuoteSchema, RANK_TIMEOUT_MS, RankResponseSchema, RankedRowSchema, ReliabilityRowSchema, SCREENER_TIMEOUT_MS, SUPPORTED_MARKET_MICS, ScreenerResponseSchema, ScreenerRowSchema, ScreenerSkippedSchema, TARGET_CURRENCIES, api, coalesceInflight, convertFX, deriveMarketState, displaySymbol, freshnessOf, friendlyAIError, getAIPerformance, getAnalytics, getAuditForecasts, getBars, getFXRate, getForecast, getHealth, getProviderBudgets, getProviderKeysStatus, getProvidersHealth, getQuote, getScreener, isFreshFxProvenance, isFxProvenanceMissingError, normalizeAIHealthTest, normalizeBarTime, normalizeBarsToCandles, normalizeHealthProviders, normalizeMarketState, normalizeRank, normalizeSymbolParam, normalizeTargetCcy, postAIInsight, rankCrossMarket, runBacktest, searchInstruments, testProviderHealth };
