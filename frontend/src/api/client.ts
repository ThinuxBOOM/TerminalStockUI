import axios, { type AxiosInstance } from 'axios';
import { z } from 'zod';

/**
 * Canonical provenance envelope — every backend data response carries this.
 * Spec §4: { source, as_of, delay_minutes, quality_grade, fallback_used, missing_fields }
 */
export const ProvenanceSchema = z.object({
  source: z.string(),
  as_of: z.string(),
  delay_minutes: z.number(),
  quality_grade: z.string(),
  fallback_used: z.boolean(),
  missing_fields: z.array(z.string()),
});
export type Provenance = z.infer<typeof ProvenanceSchema>;

export const InstrumentSchema = z.object({
  instrument_id: z.string().optional(),
  symbol: z.string(),
  exchange_mic: z.string().optional(),
  exchange_symbol: z.string().optional(),
  provider_symbol: z.string().optional(),
  company_name: z.string().optional(),
  currency: z.string().optional(),
  country: z.string().optional(),
  sector: z.string().optional(),
});
export type Instrument = z.infer<typeof InstrumentSchema>;

/* ------------------------------------------------------------------ */
/* M6: SSE market-state + currency-aware helpers (additive).             */
/* Backend `market_state` is authoritative when present; otherwise the  */
/* UI derives open|delayed|stale from the provenance envelope. `lunch`  */
/* and `closed` only ever come from the explicit API value (SSE midday  */
/* break needs the exchange calendar — never guessed client-side).      */
/* ------------------------------------------------------------------ */

export type MarketState = 'open' | 'closed' | 'lunch' | 'delayed' | 'stale';
export const MARKET_STATES: readonly MarketState[] = [
  'open',
  'closed',
  'lunch',
  'delayed',
  'stale',
];

/** Tolerant parse of backend `market_state` (any case/whitespace). Null when unknown. */
export function normalizeMarketState(v: unknown): MarketState | null {
  if (typeof v !== 'string') return null;
  const s = v.trim().toLowerCase();
  return (MARKET_STATES as readonly string[]).includes(s) ? (s as MarketState) : null;
}

/**
 * Resolve the badge state: explicit API value wins; otherwise fall back to
 * provenance-derived freshness (live→open, delayed/cached→delayed, stale→stale).
 * `closed`/`lunch` are never synthesized — they require the explicit value.
 */
export function deriveMarketState(p: Provenance, explicit?: unknown): MarketState {
  const direct = normalizeMarketState(explicit);
  if (direct) return direct;
  const f = freshnessOf(p);
  if (f === 'live') return 'open';
  if (f === 'stale') return 'stale';
  return 'delayed';
}

/** Display symbol for an instrument: provider (Yahoo-style, e.g. 600519.SS) wins. */
export function displaySymbol(
  r: Pick<Instrument, 'symbol'> & {
    provider_symbol?: string | null;
    exchange_symbol?: string | null;
  },
): string {
  const prov = (r.provider_symbol ?? '').trim();
  if (prov) return prov;
  const exch = (r.exchange_symbol ?? '').trim();
  if (exch) return exch;
  return r.symbol;
}

export const QuoteSchema = z.object({
  symbol: z.string(),
  price: z.number().nullable(),
  change: z.number().optional(),
  change_pct: z.number().optional(),
  currency: z.string().optional(),
  market_state: z.enum(['open', 'closed', 'lunch', 'delayed', 'stale']),
  instrument: InstrumentSchema.passthrough().nullable().optional(),
  ambiguous: z.boolean().optional().default(false),
  candidates: z.array(z.string()).optional().default([]),
  provenance: ProvenanceSchema,
  // Backend may nest provenance under `meta.provenance`; normalized in client.
});
export type Quote = z.infer<typeof QuoteSchema>;

export const HealthSchema = z.object({
  status: z.string(),
  providers: z
    .array(
      z.object({
        name: z.string(),
        status: z.string(),
        latency_ms: z.number().optional(),
        last_check: z.string().optional(),
      }),
    )
    .optional(),
  provenance: ProvenanceSchema.optional(),
});
export type Health = z.infer<typeof HealthSchema>;

/**
 * Resolve the API base URL.
 * - Explicit `VITE_API_BASE_URL` (trimmed, trailing `/` removed) always wins:
 *   local dev (`http://localhost:8000`) or a separate backend
 *   (`https://<host>`, no trailing slash).
 * - Unset/empty on a deployed `https:` host (e.g. Vercel) → `''` (same-origin),
 *   so relative `/api/*` calls hit the same deployment (monorepo serverless).
 * - Unset/empty on any non-localhost host → `''` (same-origin is safer than
 *   guessing localhost for previews / LAN dev).
 * - Otherwise (local `vite dev`, node tests without a browser location) →
 *   `http://localhost:8000` FastAPI default.
 */
function resolveBaseUrl(): string {
  const raw = (import.meta as unknown as { env: Record<string, string | undefined> })
    .env?.VITE_API_BASE_URL;
  const configured = (raw ?? '').trim().replace(/\/+$/, '');
  if (configured) return configured;
  if (typeof window !== 'undefined') {
    const { protocol, hostname } = window.location;
    if (protocol === 'https:') return '';
    if (hostname && hostname !== 'localhost' && hostname !== '127.0.0.1' && hostname !== '[::1]')
      return '';
  }
  return 'http://localhost:8000';
}

const BASE_URL = resolveBaseUrl();

export const api: AxiosInstance = axios.create({
  baseURL: BASE_URL,
  timeout: 15000,
  headers: { 'Content-Type': 'application/json' },
});

/** Normalize backend quote shapes → canonical Quote (handles `provenance` or `meta.provenance`). */
function normalizeQuote(raw: unknown): Quote {
  const r = raw as Record<string, unknown>;
  const prov =
    (r?.provenance as Provenance | undefined) ??
    ((r?.meta as Record<string, unknown> | undefined)?.provenance as
      | Provenance
      | undefined) ?? {
      source: 'unknown',
      as_of: new Date().toISOString(),
      delay_minutes: -1,
      quality_grade: 'U',
      fallback_used: true,
      missing_fields: ['provenance'],
    };
  const instRaw = (r?.instrument as Record<string, unknown> | null | undefined) ?? null;
  let instrument: Instrument | null = null;
  if (instRaw && typeof instRaw === 'object') {
    const parsed = InstrumentSchema.passthrough().safeParse({
      ...instRaw,
      symbol:
        (instRaw.symbol as string | undefined) ??
        (instRaw.provider_symbol as string | undefined) ??
        (instRaw.exchange_symbol as string | undefined) ??
        'UNKNOWN',
    });
    if (parsed.success) instrument = parsed.data;
  }
  const priceRaw = (r?.price as unknown) ?? (r?.last as unknown);
  const priceNum =
    priceRaw === null || priceRaw === undefined || priceRaw === ''
      ? null
      : Number(priceRaw);
  const price = priceNum === null || Number.isFinite(priceNum) ? priceNum : null;
  // Explicit market_state wins; otherwise derive from provenance (never guess closed/lunch).
  const explicit = normalizeMarketState(
    (r?.market_state as unknown) ?? (r?.marketState as unknown),
  );
  const market_state: MarketState = explicit ?? deriveMarketState(prov, null);
  const candidatesRaw = r?.candidates;
  return QuoteSchema.parse({
    symbol:
      (r?.symbol as string) ??
      (r?.ticker as string) ??
      instrument?.provider_symbol ??
      instrument?.exchange_symbol ??
      instrument?.symbol ??
      'UNKNOWN',
    price,
    change: r?.change !== undefined && r?.change !== null ? Number(r.change) : undefined,
    change_pct:
      r?.change_pct !== undefined && r?.change_pct !== null
        ? Number(r.change_pct)
        : undefined,
    currency:
      (r?.currency as string | undefined) ?? instrument?.currency ?? undefined,
    market_state,
    instrument,
    ambiguous: Boolean(r?.ambiguous ?? false),
    candidates: Array.isArray(candidatesRaw)
      ? candidatesRaw.map((c) => String(c))
      : [],
    provenance: prov,
  });
}

export async function getHealth(): Promise<Health> {
  const { data } = await api.get('/health');
  return HealthSchema.passthrough().parse(data);
}

/** Tolerant instrument normalization: accepts backend InstrumentOut (no `symbol`) as well as legacy shapes. */
function normalizeInstrument(raw: unknown): Instrument {
  const r = (raw ?? {}) as Record<string, unknown>;
  const exchange_symbol =
    (r.exchange_symbol as string | undefined) ??
    (r.symbol as string | undefined) ??
    '';
  const provider_symbol =
    (r.provider_symbol as string | undefined) ??
    (r.providerSymbol as string | undefined) ??
    '';
  // Display symbol prefers the provider (Yahoo-style) form so SSE shows
  // "600519.SS" whether the backend sent exchange_symbol "600519" or
  // provider_symbol "600519.SS".
  const symbol =
    (r.symbol as string | undefined) ||
    provider_symbol ||
    exchange_symbol ||
    'UNKNOWN';
  return InstrumentSchema.passthrough().parse({
    ...r,
    symbol,
    exchange_symbol: exchange_symbol || symbol,
    provider_symbol: provider_symbol || symbol,
  });
}

export async function searchInstruments(
  query: string,
  market?: string | null,
): Promise<Instrument[]> {
  const mic = (market ?? '').trim().toUpperCase();
  const params: Record<string, string> =
    mic && mic !== 'ALL' ? { q: query, market: mic } : { q: query };
  const { data } = await api.get('/api/instruments/search', { params });
  const list = Array.isArray(data) ? data : (data?.results ?? data?.items ?? []);
  return (list as unknown[]).map(normalizeInstrument);
}

export async function getQuote(
  symbol: string,
  market?: string | null,
  targetCcy?: string | null,
): Promise<Quote> {
  const mic = (market ?? '').trim().toUpperCase();
  const ccy = (targetCcy ?? '').trim().toUpperCase();
  const params: Record<string, string> = { symbol };
  if (mic && mic !== 'ALL') params.market = mic;
  // M7: optional target-currency for server-side conversion preview.
  // Backend may ignore it; conversion/ranking authority stays with /api/fx/*.
  if (ccy) params.target_ccy = ccy;
  const { data } = await api.get('/api/market_data/quote', { params });
  return normalizeQuote(data);
}

/** Freshness derivation used by FreshnessBadge. */
export function freshnessOf(p: Provenance): 'live' | 'delayed' | 'stale' | 'cached' {
  if (p.fallback_used) return 'cached';
  if (p.delay_minutes < 0) return 'stale';
  if (p.delay_minutes <= 1) return 'live';
  if (p.delay_minutes <= 30) return 'delayed';
  return 'stale';
}

/* ------------------------------------------------------------------ */
/* M3/M4 extension: forecast / analytics / backtest / AI (additive).   */
/* All new parsers are tolerant: backend shapes vary pre-freeze, so    */
/* normalizers accept aliases and fill a stale-marked provenance when  */
/* the envelope is absent. Callers treat throw → stale fallback.       */
/* ------------------------------------------------------------------ */

export const AI_PROFILES = [
  'Quick Insight',
  'Deep Research',
  'Forecast Assist',
  'Report',
] as const;
export type AIProfile = (typeof AI_PROFILES)[number];

export type ForecastHorizon = 5 | 21 | 63;
export const FORECAST_HORIZONS: ForecastHorizon[] = [5, 21, 63];

/** Provenance may arrive top-level or under `meta.provenance`; else stale-marked. */
function normalizeProvenance(raw: unknown, sourceFallback: string): Provenance {
  const r = (raw ?? {}) as Record<string, unknown>;
  const nested = (r.meta as Record<string, unknown> | undefined)?.provenance as
    | Provenance
    | undefined;
  const cand = (r.provenance as Provenance | undefined) ?? nested;
  if (cand && typeof cand === 'object') {
    const parsed = ProvenanceSchema.passthrough().safeParse(cand);
    if (parsed.success) return parsed.data;
  }
  return {
    source: (r.source as string | undefined) ?? sourceFallback,
    as_of: (r.as_of as string | undefined) ?? new Date().toISOString(),
    delay_minutes:
      typeof (r.delay_minutes ?? (cand as Record<string, unknown> | undefined)?.delay_minutes) ===
      'number'
        ? Number(r.delay_minutes ?? (cand as Record<string, unknown>).delay_minutes)
        : -1,
    quality_grade:
      (r.quality_grade as string | undefined) ??
      (r.data_quality as string | undefined) ??
      'U',
    fallback_used: true,
    missing_fields: ['provenance'],
  };
}

function strArray(v: unknown): string[] {
  if (Array.isArray(v)) return v.map((x) => String(x));
  if (v === undefined || v === null) return [];
  return [String(v)];
}

function num(v: unknown, fallback = Number.NaN): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

/* ------------------------- reliability rows ------------------------ */

export const ReliabilityRowSchema = z
  .object({
    bin_low: z.number(),
    bin_high: z.number(),
    count: z.number(),
    mean_predicted: z.number().nullable().optional(),
    fraction_positive: z.number().nullable().optional(),
  })
  .passthrough();
export type ReliabilityRow = z.infer<typeof ReliabilityRowSchema>;

function normalizeReliability(v: unknown): ReliabilityRow[] {
  if (!Array.isArray(v)) return [];
  const out: ReliabilityRow[] = [];
  for (const row of v) {
    const parsed = ReliabilityRowSchema.safeParse(row);
    if (parsed.success) out.push(parsed.data);
  }
  return out;
}

/* ------------------------------ forecast --------------------------- */

export const ForecastSchema = z
  .object({
    symbol: z.string(),
    horizon_days: z.number().optional().default(21),
    label: z.string().optional().default(''),
    probability: z.number().min(0).max(1),
    confidence: z.string().optional().default('Unknown'),
    quality_grade: z.string().optional().default('U'),
    provider: z.string().optional().default('deterministic-engine'),
    why: z.array(z.string()).optional().default([]),
    risks: z.array(z.string()).optional().default([]),
    evidence_ids: z.array(z.string()).optional().default([]),
    inputs: z.record(z.unknown()).optional(),
    versions: z.record(z.unknown()).optional(),
    calibration: z.array(ReliabilityRowSchema).optional().default([]),
    intervals: z
      .object({ low: z.number(), mid: z.number(), high: z.number() })
      .passthrough()
      .nullable()
      .optional(),
    limitations: z.array(z.string()).optional().default([]),
    disclosure: z.string().optional(),
    provenance: ProvenanceSchema,
  })
  .passthrough();
export type Forecast = z.infer<typeof ForecastSchema>;

function normalizeForecast(raw: unknown, symbol: string, horizon: number): Forecast {
  const r = (raw ?? {}) as Record<string, unknown>;
  const versions =
    (r.versions as Record<string, unknown> | undefined) ?? {
      ...(typeof r.model_name === 'string' ? { model_name: r.model_name } : {}),
      ...(typeof r.model_version === 'string' ? { model_version: r.model_version } : {}),
      ...(typeof r.feature_version === 'string' ? { feature_version: r.feature_version } : {}),
      ...(typeof r.data_version === 'string' ? { data_version: r.data_version } : {}),
      ...(typeof r.as_of === 'string' ? { as_of: r.as_of } : {}),
    };
  const direction = typeof r.direction === 'string' ? r.direction : undefined;
  const label =
    (r.label as string | undefined) ??
    (r.outlook as string | undefined) ??
    (direction ? `${direction}, ${num(r.horizon_days ?? r.horizon ?? horizon, horizon)}d` : '');
  const intervalsRaw =
    (r.intervals as Record<string, unknown> | undefined) ??
    (r.expected_return_range as Record<string, unknown> | undefined) ??
    (r.return_range as Record<string, unknown> | undefined) ??
    null;
  const candidate = {
    symbol: (r.symbol as string | undefined) ?? (r.ticker as string | undefined) ?? symbol,
    horizon_days: num(r.horizon_days ?? r.horizon ?? horizon, horizon),
    label,
    probability: num(
      r.probability ?? r.direction_probability ?? r.proba ?? (r.value as unknown),
      Number.NaN,
    ),
    confidence: (r.confidence as string | undefined) ?? (r.confidence_level as string | undefined) ?? 'Unknown',
    quality_grade:
      (r.quality_grade as string | undefined) ??
      (r.data_quality as string | undefined) ??
      (r.grade as string | undefined) ??
      'U',
    provider:
      (r.provider as string | undefined) ??
      (r.model_name as string | undefined) ??
      'deterministic-engine',
    why: strArray(r.why ?? r.bull ?? r.bullish_signals ?? r.top_bullish ?? r.catalysts),
    risks: strArray(r.risks ?? r.bear ?? r.bearish_risks ?? r.top_risks),
    evidence_ids: strArray(r.evidence_ids ?? r.evidence ?? []),
    inputs:
      (r.inputs as Record<string, unknown> | undefined) ??
      (r.features as Record<string, unknown> | undefined),
    versions: Object.keys(versions).length > 0 ? versions : undefined,
    calibration: normalizeReliability(
      r.calibration ?? r.calibration_history ?? r.reliability ?? r.reliability_table ?? [],
    ),
    intervals:
      intervalsRaw &&
      Number.isFinite(Number((intervalsRaw as Record<string, unknown>).low)) &&
      Number.isFinite(Number((intervalsRaw as Record<string, unknown>).mid)) &&
      Number.isFinite(Number((intervalsRaw as Record<string, unknown>).high))
        ? {
            low: Number((intervalsRaw as Record<string, unknown>).low),
            mid: Number((intervalsRaw as Record<string, unknown>).mid),
            high: Number((intervalsRaw as Record<string, unknown>).high),
          }
        : null,
    limitations: strArray(r.limitations),
    disclosure: r.disclosure as string | undefined,
    provenance: normalizeProvenance(r, 'forecast-api'),
  };
  return ForecastSchema.parse(candidate);
}

/** GET /api/forecast/{symbol}?horizon=21 (path-style, backend contract).
 *  Falls back to legacy query-style /api/forecast?symbol=&horizon= so older
 *  backends keep working. Same signature, same Forecast return. */
export async function getForecast(symbol: string, horizon = 21): Promise<Forecast> {
  const sym = String(symbol ?? '').trim();
  try {
    const { data } = await api.get(`/api/forecast/${encodeURIComponent(sym)}`, {
      params: { horizon },
    });
    return normalizeForecast(data, sym, horizon);
  } catch (pathErr) {
    try {
      const { data } = await api.get('/api/forecast', { params: { symbol: sym, horizon } });
      return normalizeForecast(data, sym, horizon);
    } catch {
      throw pathErr;
    }
  }
}

/* ------------------------------ analytics -------------------------- */

export const AnalyticsSchema = z
  .object({
    symbol: z.string(),
    technical: z.record(z.unknown()).optional().default({}),
    fundamentals: z.record(z.unknown()).optional().default({}),
    quality: z.record(z.unknown()).optional().default({}),
    valuation: z.record(z.unknown()).optional().default({}),
    provenance: ProvenanceSchema,
  })
  .passthrough();
export type Analytics = z.infer<typeof AnalyticsSchema>;

function normalizeAnalytics(raw: unknown, symbol: string): Analytics {
  const r = (raw ?? {}) as Record<string, unknown>;
  const nested = (r.data ?? r.analytics ?? {}) as Record<string, unknown>;
  const pick = (key: string) =>
    (r[key] as Record<string, unknown> | undefined) ??
    (nested[key] as Record<string, unknown> | undefined) ??
    {};
  const candidate = {
    symbol: (r.symbol as string | undefined) ?? (nested.symbol as string | undefined) ?? symbol,
    technical: pick('technical'),
    fundamentals: pick('fundamentals'),
    quality: pick('quality'),
    valuation: pick('valuation'),
    provenance: normalizeProvenance({ ...nested, ...r }, 'analytics-api'),
  };
  return AnalyticsSchema.parse(candidate);
}

/** GET /api/analytics/{symbol} (path-style, backend contract).
 *  Falls back to legacy query-style /api/analytics?symbol= so older
 *  backends keep working. Same signature, same Analytics return. */
export async function getAnalytics(symbol: string): Promise<Analytics> {
  const sym = String(symbol ?? '').trim();
  try {
    const { data } = await api.get(`/api/analytics/${encodeURIComponent(sym)}`);
    return normalizeAnalytics(data, sym);
  } catch (pathErr) {
    try {
      const { data } = await api.get('/api/analytics', { params: { symbol: sym } });
      return normalizeAnalytics(data, sym);
    } catch {
      throw pathErr;
    }
  }
}

/* ------------------------------- backtest -------------------------- */

export const BacktestSchema = z
  .object({
    symbol: z.string(),
    horizons: z.array(z.number()).optional().default([]),
    brier: z.number().nullable().optional(),
    ece: z.number().nullable().optional(),
    reliability: z.array(ReliabilityRowSchema).optional().default([]),
    n_windows: z.number().nullable().optional(),
    failures: z.array(z.string()).optional().default([]),
    notes: z.string().optional(),
    provenance: ProvenanceSchema,
  })
  .passthrough();
export type Backtest = z.infer<typeof BacktestSchema>;

function normalizeBacktest(raw: unknown, symbol: string, horizons: number[]): Backtest {
  const r = (raw ?? {}) as Record<string, unknown>;
  const brierRaw = r.brier ?? r.brier_score;
  const eceRaw = r.ece ?? r.calibration_error ?? r.ece_score;
  const candidate = {
    symbol: (r.symbol as string | undefined) ?? symbol,
    horizons:
      (Array.isArray(r.horizons) ? (r.horizons as unknown[]) : horizons).map((h) => Number(h)) ??
      horizons,
    brier: brierRaw === undefined || brierRaw === null ? null : num(brierRaw, Number.NaN),
    ece: eceRaw === undefined || eceRaw === null ? null : num(eceRaw, Number.NaN),
    reliability: normalizeReliability(r.reliability ?? r.reliability_table ?? r.table ?? []),
    n_windows:
      r.n_windows === undefined || r.n_windows === null
        ? (r.n === undefined || r.n === null ? null : num(r.n, Number.NaN))
        : num(r.n_windows, Number.NaN),
    failures: strArray(r.failures ?? r.errors ?? []),
    notes: r.notes as string | undefined,
    provenance: normalizeProvenance(r, 'backtest-api'),
  };
  const parsed = BacktestSchema.parse(candidate);
  return {
    ...parsed,
    brier: parsed.brier !== undefined && Number.isNaN(parsed.brier) ? null : parsed.brier,
    ece: parsed.ece !== undefined && Number.isNaN(parsed.ece) ? null : parsed.ece,
  };
}

/** POST /api/backtest/run { symbol, horizons } — walk-forward, lightweight only.
 *  Falls back to legacy POST /api/backtest. The /run shape is per-horizon
 *  ({results: {21: {brier, ece, reliability, ...}}}); it is flattened to the
 *  single-horizon Backtest view using the first requested horizon. */
export async function runBacktest(symbol: string, horizons: number[]): Promise<Backtest> {
  const sym = String(symbol ?? '').trim();
  try {
    const { data } = await api.post('/api/backtest/run', { symbol: sym, horizons });
    return normalizeBacktest(flattenBacktestRun(data, sym, horizons), sym, horizons);
  } catch (runErr) {
    try {
      const { data } = await api.post('/api/backtest', { symbol: sym, horizons });
      return normalizeBacktest(flattenBacktestRun(data, sym, horizons), sym, horizons);
    } catch {
      throw runErr;
    }
  }
}

/** Map the /run per-horizon payload → flat Backtest shape (legacy passthrough). */
function flattenBacktestRun(raw: unknown, symbol: string, horizons: number[]): unknown {
  const r = (raw ?? {}) as Record<string, unknown>;
  const results = r.results as Record<string, Record<string, unknown>> | undefined;
  if (results && typeof results === 'object' && !Array.isArray(results)) {
    const first = String(horizons[0] ?? Object.keys(results)[0] ?? '');
    const h = (results[first] ?? results[String(Number(first))] ?? {}) as Record<string, unknown>;
    const nWindows = h.n_points ?? h.n_folds ?? r.n_windows ?? r.n ?? null;
    return {
      ...(r as Record<string, unknown>),
      symbol: (r.symbol as string | undefined) ?? symbol,
      horizons,
      brier: h.brier ?? r.brier ?? r.brier_score ?? null,
      ece: h.ece ?? r.ece ?? r.calibration_error ?? null,
      reliability: h.reliability ?? r.reliability ?? r.reliability_table ?? r.table ?? [],
      n_windows: nWindows,
      failures: r.failures ?? r.errors ?? [],
      notes: r.notes as string | undefined,
      provenance: (r.provenance as Provenance | undefined) ?? normalizeProvenance(r, 'backtest-api'),
    };
  }
  return raw;
}

/* ------------------------------- AI opinion ------------------------ */

export const AIOpinionSchema = z
  .object({
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
    provenance: ProvenanceSchema.optional(),
  })
  .passthrough();
export type AIOpinion = z.infer<typeof AIOpinionSchema>;

function normalizeAIOpinion(raw: unknown): AIOpinion {
  const r = (raw ?? {}) as Record<string, unknown>;
  const opinion = (r.opinion ?? r.data ?? r) as Record<string, unknown>;
  const candidate = {
    ...(opinion as Record<string, unknown>),
    direction: (opinion.direction as string | undefined) ?? (opinion.outlook as string | undefined),
    probability: num(opinion.probability ?? opinion.proba, Number.NaN),
    time_horizon_days: num(
      opinion.time_horizon_days ?? opinion.horizon_days ?? opinion.horizon,
      Number.NaN,
    ),
    catalysts: strArray(opinion.catalysts),
    risks: strArray(opinion.risks),
    // evidence_ids intentionally NOT defaulted: missing IDs must fail validation
    // (spec M5: reject claims without evidence IDs).
    limitations: strArray(opinion.limitations),
  };
  return AIOpinionSchema.parse(candidate);
}

/**
 * POST /api/ai/insight { symbol, profile } — explicit AI calls only.
 * Never overrides the deterministic core; weight capped at 20% (see card).
 *
 * Own timeout (60s, not the shared 15s): a cold serverless function plus a
 * thinking model (large evidence prompt, up to 2048 output tokens) routinely
 * exceeds 15s on first call. Repeats are served from the evidence-hash
 * cache and return fast.
 */
export const AI_TIMEOUT_MS = 60000;

export async function postAIInsight(symbol: string, profile: AIProfile): Promise<AIOpinion> {
  const { data } = await api.post(
    '/api/ai/insight',
    { symbol, profile },
    { timeout: AI_TIMEOUT_MS },
  );
  return normalizeAIOpinion(data);
}

/** Human message for AI request failures (timeout-aware). */
export function friendlyAIError(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error ?? 'unknown error');
  if ((error as { code?: unknown })?.code === 'ECONNABORTED' || /timeout of \d+ms exceeded/i.test(message)) {
    return 'AI took longer than 60s (cold start + thinking model) — deterministic forecast unaffected. Retry; repeat calls are usually instant via the evidence cache.';
  }
  return `AI request failed (${message}).`;
}

/* ---------------------------- AI performance ----------------------- */

export const AIPerformanceRowSchema = z
  .object({
    provider: z.string(),
    model: z.string().optional().default(''),
    exchange: z.string().optional().default(''),
    horizon_days: z.number().optional(),
    n_calls: z.number().optional().default(0),
    brier: z.number().nullable().optional(),
    ece: z.number().nullable().optional(),
    hit_rate: z.number().nullable().optional(),
  })
  .passthrough();
export type AIPerformanceRow = z.infer<typeof AIPerformanceRowSchema>;

function normalizeAIPerformance(raw: unknown): AIPerformanceRow[] {
  const r = raw as Record<string, unknown> | unknown[];
  const list = Array.isArray(r)
    ? r
    : ((r as Record<string, unknown>)?.rows as unknown) ??
      ((r as Record<string, unknown>)?.performance as unknown) ??
      ((r as Record<string, unknown>)?.providers as unknown) ??
      [];
  if (!Array.isArray(list)) return [];
  const out: AIPerformanceRow[] = [];
  for (const row of list) {
    const rr = row as Record<string, unknown>;
    const candidate = {
      ...(rr as Record<string, unknown>),
      provider: (rr.provider as string | undefined) ?? (rr.name as string | undefined),
      model: (rr.model as string | undefined) ?? '',
      n_calls: rr.n_calls ?? rr.total_calls ?? rr.calls_1h ?? 0,
      brier: (rr.brier as number | undefined) ?? null,
      ece: (rr.ece as number | undefined) ?? (rr.calibration_error as number | undefined) ?? null,
      hit_rate: (rr.hit_rate as number | undefined) ?? (rr.accuracy as number | undefined) ?? null,
    };
    const parsed = AIPerformanceRowSchema.safeParse(candidate);
    if (parsed.success) out.push(parsed.data);
  }
  return out;
}

/** GET /api/ai/providers/performance — historical provider/model scores by exchange+horizon.
 *  Falls back to legacy GET /api/ai/performance. */
export async function getAIPerformance(): Promise<AIPerformanceRow[]> {
  try {
    const { data } = await api.get('/api/ai/providers/performance');
    return normalizeAIPerformance(data);
  } catch (pathErr) {
    try {
      const { data } = await api.get('/api/ai/performance');
      return normalizeAIPerformance(data);
    } catch {
      throw pathErr;
    }
  }
}

/* ---------------------------- provider health ---------------------- */

export type ProviderHealthTest = {
  ok: boolean;
  latency_ms?: number;
  message?: string;
  provider?: string;
};

/**
 * Health probe. Primary: POST /api/providers/health/test (backend contract);
 * fallback: POST /api/ai/test. Never sends or returns API keys.
 */
export async function testProviderHealth(
  provider: string,
  profile?: string,
): Promise<ProviderHealthTest> {
  try {
    const { data } = await api.post(
      '/api/providers/health/test',
      { provider, profile },
      { params: { provider } },
    );
    const d = (data ?? {}) as Record<string, unknown>;
    if (typeof d.ok === 'boolean')
      return { ok: d.ok as boolean, latency_ms: d.latency_ms as number | undefined, message: d.message as string | undefined, provider };
    const total = Number(d.total_calls ?? 0);
    return {
      ok: (d.circuit as string | undefined) !== 'open',
      latency_ms: d.latency_p50_ms !== undefined ? Number(d.latency_p50_ms) : undefined,
      message: total > 0 ? `probe recorded (${total} total calls)` : 'probe recorded',
      provider: (d.provider as string | undefined) ?? provider,
    };
  } catch (primaryErr) {
    const { data } = await api.post('/api/ai/test', { provider, profile });
    const d = (data ?? {}) as Record<string, unknown>;
    if (typeof d.ok === 'boolean')
      return { ok: d.ok as boolean, latency_ms: d.latency_ms as number | undefined, message: d.message as string | undefined, provider };
    throw primaryErr;
  }
}

/* ------------------------------------------------------------------ */
/* M7: Euronext markets + FX-gated cross-market comparison (additive).  */
/* Backend `backend/api/fx.py` owns rate/convert/rank; this client only */
/* threads MIC + target-ccy params and enforces the provenance gate in */
/* the UI (never rank without fresh FX). All parsers are tolerant.     */
/* ------------------------------------------------------------------ */

/** M7 market filter domain (M6 + Euronext). `ALL`/empty omits `?market=`. */
export const EURONEXT_MICS = ['XPAR', 'XAMS', 'XBRU'] as const;
export type EuronextMic = (typeof EURONEXT_MICS)[number];

export const SUPPORTED_MARKET_MICS = [
  'XNYS',
  'XNAS',
  'XSHG',
  'XPAR',
  'XAMS',
  'XBRU',
] as const;
export type SupportedMarketMic = (typeof SUPPORTED_MARKET_MICS)[number];

/** Target currencies offered by the FX-gated Watchlist. */
export const TARGET_CURRENCIES = ['USD', 'EUR', 'CNY'] as const;
export type TargetCurrency = (typeof TARGET_CURRENCIES)[number];

export function normalizeTargetCcy(v: unknown): TargetCurrency {
  const s = String(v ?? '').trim().toUpperCase();
  return (TARGET_CURRENCIES as readonly string[]).includes(s)
    ? (s as TargetCurrency)
    : 'USD';
}

/** Gate error code: backend refuses ranking without solid FX provenance. */
export const FX_PROVENANCE_MISSING = 'FX_PROVENANCE_MISSING';

/** True when an axios/fetch error carries the FX provenance gate code. */
export function isFxProvenanceMissingError(err: unknown): boolean {
  const e = err as {
    response?: { data?: unknown; status?: number };
    data?: unknown;
    code?: unknown;
    message?: unknown;
  };
  const payload = (e?.response?.data ?? e?.data ?? {}) as Record<string, unknown>;
  const nested = (payload.error ?? {}) as Record<string, unknown>;
  const code =
    (nested.code as string | undefined) ??
    (payload.code as string | undefined) ??
    (typeof e?.code === 'string' ? (e.code as string) : undefined);
  if (typeof code === 'string' && code.toUpperCase().includes(FX_PROVENANCE_MISSING))
    return true;
  const msg = String(
    (nested.message as string | undefined) ??
      (payload.message as string | undefined) ??
      (payload.detail as string | undefined) ??
      (e?.message as string | undefined) ??
      '',
  );
  return msg.toUpperCase().includes(FX_PROVENANCE_MISSING);
}

/**
 * Fresh-FX rule (spec M7/M8: rank ONLY after FX provenance is solid).
 * Fresh = envelope present, not fallback, delay 0..30m, grade A/B.
 * Anything else → gated (Watchlist shows the unavailable message).
 */
export function isFreshFxProvenance(p: Provenance | null | undefined): boolean {
  if (!p || typeof p !== 'object') return false;
  if (p.fallback_used) return false;
  if (typeof p.delay_minutes !== 'number' || p.delay_minutes < 0 || p.delay_minutes > 30)
    return false;
  const grade = String(p.quality_grade ?? '').trim().toUpperCase();
  return grade === 'A' || grade === 'B';
}

/* ------------------------------- FX types -------------------------- */

export const FXRateSchema = z
  .object({
    base: z.string(),
    quote: z.string(),
    rate: z.number(),
    provenance: ProvenanceSchema,
  })
  .passthrough();
export type FXRate = z.infer<typeof FXRateSchema>;

export const FXConvertResultSchema = z
  .object({
    amount: z.number(),
    from: z.string(),
    to: z.string(),
    converted: z.number().nullable(),
    rate: z.number().nullable().optional(),
    provenance: ProvenanceSchema,
  })
  .passthrough();
export type FXConvertResult = z.infer<typeof FXConvertResultSchema>;

export const RankedRowSchema = z
  .object({
    symbol: z.string(),
    price: z.number().nullable().optional(),
    currency: z.string().optional(),
    converted_price: z.number().nullable().optional(),
    target_ccy: z.string().optional(),
    change_pct: z.number().nullable().optional(),
    market_state: z.string().nullable().optional(),
    instrument: InstrumentSchema.passthrough().nullable().optional(),
    provenance: ProvenanceSchema,
  })
  .passthrough();
export type RankedRow = z.infer<typeof RankedRowSchema>;

export const RankResponseSchema = z
  .object({
    target_ccy: z.string(),
    ranking: z.array(RankedRowSchema),
    fx_provenance: ProvenanceSchema.nullable(),
  })
  .passthrough();
export type RankResponse = z.infer<typeof RankResponseSchema> & {
  symbols: string[];
};

function ccy(v: unknown, fallback: string): string {
  const s = String(v ?? fallback).trim().toUpperCase();
  return /^[A-Z]{3}$/.test(s) ? s : fallback;
}

function rateNumber(v: unknown): number | null {
  if (v === null || v === undefined || v === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function normalizeFXRate(raw: unknown, base: string, quote: string): FXRate {
  const r = (raw ?? {}) as Record<string, unknown>;
  const rate =
    rateNumber(r.rate ?? r.fx_rate ?? r.price ?? r.value) ??
    Number.NaN;
  return FXRateSchema.parse({
    ...(r as Record<string, unknown>),
    base: ccy(r.base ?? r.from ?? base, ccy(base, 'EUR')),
    quote: ccy(r.quote ?? r.to ?? r.target_ccy ?? quote, ccy(quote, 'USD')),
    rate,
    provenance: normalizeProvenance(r, 'fx-api'),
  });
}

/**
 * GET /api/fx/rate?base=EUR&quote=USD
 * Returns the pair rate + its provenance envelope. Throws (passthrough)
 * on FX_PROVENANCE_MISSING so callers can render the gate message.
 */
export async function getFXRate(base: string, quote: string): Promise<FXRate> {
  const b = ccy(base, 'EUR');
  const q = ccy(quote, 'USD');
  const { data } = await api.get('/api/fx/rate', { params: { base: b, quote: q } });
  return normalizeFXRate(data, b, q);
}

function normalizeConvert(
  raw: unknown,
  amount: number,
  from: string,
  to: string,
): FXConvertResult {
  const r = (raw ?? {}) as Record<string, unknown>;
  const converted =
    rateNumber(
      r.converted ?? r.converted_amount ?? r.result ?? r.value ?? r.price,
    ) ?? null;
  const rate = rateNumber(r.rate ?? r.fx_rate);
  return FXConvertResultSchema.parse({
    ...(r as Record<string, unknown>),
    amount: Number.isFinite(Number(r.amount ?? amount)) ? Number(r.amount ?? amount) : amount,
    from: ccy(r.from ?? r.base ?? from, ccy(from, 'EUR')),
    to: ccy(r.to ?? r.quote ?? r.target_ccy ?? to, ccy(to, 'USD')),
    converted,
    rate,
    provenance: normalizeProvenance(r, 'fx-api'),
  });
}

/**
 * POST /api/fx/convert { amount, from, to }
 * Single-amount conversion with provenance. Gate errors passthrough.
 */
export async function convertFX(
  amount: number,
  from: string,
  to: string,
): Promise<FXConvertResult> {
  const f = ccy(from, 'EUR');
  const t = ccy(to, 'USD');
  const { data } = await api.post('/api/fx/convert', {
    amount,
    from: f,
    to: t,
  });
  return normalizeConvert(data, amount, f, t);
}

function normalizeRankedRow(raw: unknown, targetCcy: string): RankedRow {
  const r = (raw ?? {}) as Record<string, unknown>;
  const instRaw = (r.instrument as Record<string, unknown> | null | undefined) ?? null;
  let instrument: Instrument | null = null;
  if (instRaw && typeof instRaw === 'object') {
    const parsed = InstrumentSchema.passthrough().safeParse({
      ...instRaw,
      symbol:
        (instRaw.symbol as string | undefined) ??
        (instRaw.provider_symbol as string | undefined) ??
        (instRaw.exchange_symbol as string | undefined) ??
        (r.symbol as string | undefined) ??
        'UNKNOWN',
    });
    if (parsed.success) instrument = parsed.data;
  }
  const price = rateNumber(r.price ?? r.last);
  const converted = rateNumber(
    r.converted_price ?? r.converted ?? r.price_in_target ?? r.target_price,
  );
  const changePct = rateNumber(r.change_pct ?? r.changePct);
  return RankedRowSchema.parse({
    ...(r as Record<string, unknown>),
    symbol:
      (r.symbol as string | undefined) ??
      (r.ticker as string | undefined) ??
      instrument?.provider_symbol ??
      instrument?.symbol ??
      'UNKNOWN',
    price,
    currency:
      (r.currency as string | undefined) ?? instrument?.currency ?? undefined,
    converted_price: converted,
    target_ccy: ccy(r.target_ccy ?? r.targetCcy ?? targetCcy, ccy(targetCcy, 'USD')),
    change_pct: changePct,
    market_state:
      (r.market_state as string | undefined) ??
      (r.marketState as string | undefined) ??
      undefined,
    instrument,
    provenance: normalizeProvenance(r, 'market-data-api'),
  });
}

function normalizeRank(
  raw: unknown,
  symbols: string[],
  targetCcy: string,
): RankResponse {
  const r = (raw ?? {}) as Record<string, unknown>;
  const listRaw = Array.isArray(r.ranking)
    ? r.ranking
    : Array.isArray(r.results)
      ? r.results
      : Array.isArray(r.items)
        ? r.items
        : Array.isArray(r.rows)
          ? r.rows
          : [];
  const ranking = (listRaw as unknown[]).map((row) =>
    normalizeRankedRow(row, targetCcy),
  );
  const fxRaw =
    (r.fx_provenance as unknown) ??
    (r.fxProvenance as unknown) ??
    ((r.fx as Record<string, unknown> | undefined)?.provenance as unknown) ??
    (r.provenance as unknown) ??
    null;
  let fx_provenance: Provenance | null = null;
  if (fxRaw && typeof fxRaw === 'object') {
    const parsed = ProvenanceSchema.passthrough().safeParse(fxRaw);
    if (parsed.success) fx_provenance = parsed.data;
  }
  const target = ccy(
    r.target_ccy ?? r.targetCcy ?? r.to ?? targetCcy,
    ccy(targetCcy, 'USD'),
  );
  const parsed = RankResponseSchema.parse({
    ...(r as Record<string, unknown>),
    target_ccy: target,
    ranking,
    fx_provenance,
  });
  return { ...parsed, symbols: [...symbols] };
}

/**
 * POST /api/fx/rank { symbols, target_ccy }
 * Cross-market ranking in `target_ccy`. NEVER call for display unless
 * `isFreshFxProvenance(fx_provenance)` holds; on HTTP 409/502 with
 * `FX_PROVENANCE_MISSING` the error is rethrown untouched so the
 * Watchlist can render "Cross-market comparison unavailable — FX
 * provenance missing" instead of ranked numbers.
 */
export async function rankCrossMarket(
  symbols: string[],
  targetCcy: string,
): Promise<RankResponse> {
  const target = ccy(targetCcy, 'USD');
  const clean = symbols.map((s) => String(s ?? '').trim()).filter(Boolean);
  const { data } = await api.post('/api/fx/rank', {
    symbols: clean,
    target_ccy: target,
  });
  return normalizeRank(data, clean, target);
}

/* ------------------------------------------------------------------ */
/* M8: home dashboard helpers (additive — no existing export changed).  */
/* ------------------------------------------------------------------ */

export type ProviderHealthSummary = {
  name: string;
  status: string;
  latency_ms?: number;
  latency_p50_ms?: number;
  latency_p95_ms?: number;
  error_rate_1h?: number;
  calls_1h?: number;
  total_calls?: number;
  circuit?: string;
  last_check?: string;
};

function numOrUndef(v: unknown): number | undefined {
  if (v === undefined || v === null || v === '') return undefined;
  const n = Number(v);
  return Number.isFinite(n) ? n : undefined;
}

/**
 * GET /api/providers/health → per-provider latency/error/circuit state.
 * Tolerant: accepts {providers:[...]} or a bare array; accepts both
 * {provider,...} (backend) and {name,...} (health summary) row shapes.
 * Callers treat throw → stale fallback (cached /health values).
 */
export async function getProvidersHealth(): Promise<ProviderHealthSummary[]> {
  const { data } = await api.get('/api/providers/health');
  const raw = (data ?? {}) as Record<string, unknown>;
  const list = Array.isArray(data)
    ? (data as unknown[])
    : Array.isArray(raw.providers)
      ? (raw.providers as unknown[])
      : [];
  return (list as Record<string, unknown>[]).map((row) => {
    const name = String(
      row.provider ?? row.name ?? row.id ?? 'unknown',
    );
    const circuit =
      typeof row.circuit === 'string' ? (row.circuit as string) : undefined;
    const latencyP50 = numOrUndef(row.latency_p50_ms ?? row.latency_ms);
    const status =
      typeof row.status === 'string'
        ? (row.status as string)
        : circuit === 'open'
          ? 'open'
          : 'ok';
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
      last_check:
        typeof row.last_check === 'string' ? (row.last_check as string) : undefined,
    };
  });
}

export type AuditForecast = {
  forecast_id?: string;
  symbol?: string;
  horizon_days?: number;
  direction_probability?: number | null;
  confidence?: string;
  model_version?: string;
  feature_version?: string;
  data_version?: string;
  target_date?: string;
  created_at?: string;
};

export type AuditForecastsResult = {
  forecasts: AuditForecast[];
  count: number;
  disclosure: string;
};

/**
 * GET /api/audit/forecasts?limit=N → recent versioned forecast log.
 * Powers Home "Latest research". Empty log → {forecasts: []} (the page
 * renders "No reports yet"). Throws on transport error so the page can
 * render its error/stale state.
 */
export async function getAuditForecasts(limit = 5): Promise<AuditForecastsResult> {
  const n = Number.isFinite(Number(limit)) ? Math.min(200, Math.max(1, Number(limit))) : 5;
  const { data } = await api.get('/api/audit/forecasts', { params: { limit: n } });
  const raw = (data ?? {}) as Record<string, unknown>;
  const listRaw = Array.isArray(data)
    ? (data as unknown[])
    : Array.isArray(raw.forecasts)
      ? (raw.forecasts as unknown[])
      : Array.isArray(raw.results)
        ? (raw.results as unknown[])
        : [];
  const forecasts = (listRaw as Record<string, unknown>[]).map((f) => ({
    ...(f as Record<string, unknown>),
    forecast_id:
      typeof f.forecast_id === 'string' ? (f.forecast_id as string) : undefined,
    symbol: typeof f.symbol === 'string' ? (f.symbol as string) : undefined,
    horizon_days:
      f.horizon_days === undefined || f.horizon_days === null
        ? undefined
        : Number(f.horizon_days),
    direction_probability:
      f.direction_probability === undefined || f.direction_probability === null
        ? null
        : numOrUndef(f.direction_probability) ?? null,
    confidence: typeof f.confidence === 'string' ? (f.confidence as string) : undefined,
    model_version:
      typeof f.model_version === 'string' ? (f.model_version as string) : undefined,
    feature_version:
      typeof f.feature_version === 'string' ? (f.feature_version as string) : undefined,
    data_version:
      typeof f.data_version === 'string' ? (f.data_version as string) : undefined,
    target_date:
      typeof f.target_date === 'string' ? (f.target_date as string) : undefined,
    created_at:
      typeof f.created_at === 'string' ? (f.created_at as string) : undefined,
  }));
  return {
    forecasts,
    count: typeof raw.count === 'number' ? (raw.count as number) : forecasts.length,
    disclosure:
      typeof raw.disclosure === 'string'
        ? (raw.disclosure as string)
        : 'Not investment advice. For informational purposes only.',
  };
}
