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

/* ------------------- frontend data-layer hardening ------------------- */
/** Canonical symbol normalization: trim, strip inner whitespace, UPPER. */
export function normalizeSymbolParam(v: unknown): string {
  return String(v ?? '')
    .trim()
    .toUpperCase()
    .replace(/\s+/g, '');
}

function httpStatus(err: unknown): number | null {
  const e = err as { response?: { status?: unknown }; status?: unknown } | null;
  const s = e?.response?.status ?? e?.status;
  return typeof s === 'number' ? s : null;
}

/** 404/501 = endpoint not yet deployed → legacy fallback. Others rethrow. */
function isEndpointMissingError(err: unknown): boolean {
  const s = httpStatus(err);
  return s === 404 || s === 501;
}

/**
 * Coalesce identical in-flight requests: concurrent callers with the same
 * key share one network promise (deleted on settle, so sequential calls
 * still refetch). Complements TanStack query-key sharing for call sites
 * that use divergent keys for the same resource.
 */
const inflight = new Map<string, Promise<never>>();

export function coalesceInflight<T>(key: string, fn: () => Promise<T>): Promise<T> {
  const hit = inflight.get(key) as Promise<T> | undefined;
  if (hit) return hit;
  const p = fn().finally(() => {
    if (inflight.get(key) === (p as Promise<never>)) inflight.delete(key);
  }) as Promise<T>;
  inflight.set(key, p as Promise<never>);
  return p;
}

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

/** Normalize `/health` provider rows to the HealthSchema shape.
 * Backend tracker rows are {provider, latency_p50_ms, ..., circuit} with
 * no name/status; without this mapping parsing succeeds only on the
 * empty-tracker shape and throws once any call is recorded. */
export function normalizeHealthProviders(raw: unknown): Record<string, unknown>[] {
  if (!Array.isArray(raw)) return [];
  return (raw as Record<string, unknown>[]).map((row) => {
    const circuit = typeof row.circuit === 'string' ? row.circuit : undefined;
    return {
      ...row,
      name: row.name ?? row.provider ?? 'unknown',
      status:
        typeof row.status === 'string'
          ? row.status
          : circuit === 'open'
            ? 'open'
            : 'ok',
      latency_ms: row.latency_ms ?? row.latency_p50_ms,
    };
  });
}

export async function getHealth(): Promise<Health> {
  return coalesceInflight('health', async () => {
    const { data } = await api.get('/health');
    const raw = (data ?? {}) as Record<string, unknown>;
    if (Array.isArray(raw.providers)) {
      raw.providers = normalizeHealthProviders(raw.providers);
    }
    // Tolerant: providers rows are normalized above; a missing/invalid
    // status still parses via passthrough defaults where possible.
    // Transport errors rethrow so ErrorState shows.
    return HealthSchema.passthrough().parse(data);
  });
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
  const q = String(query ?? '').trim();
  // Blank query → [] without network (avoids fan-out on empty input).
  if (!q) return [];
  const mic = String(market ?? '').trim().toUpperCase();
  const params: Record<string, string> =
    mic && mic !== 'ALL' ? { q, market: mic } : { q };
  return coalesceInflight(`search:${q.toLowerCase()}:${mic || 'ALL'}`, async () => {
    const { data } = await api.get('/api/instruments/search', { params });
    const list = Array.isArray(data) ? data : (data?.results ?? data?.items ?? []);
    // Tolerant: skip single bad rows instead of failing the whole search
    // on backend shape drift.
    const out: Instrument[] = [];
    for (const row of list as unknown[]) {
      try {
        out.push(normalizeInstrument(row));
      } catch {
        continue;
      }
    }
    return out;
  });
}

export async function getQuote(
  symbol: string,
  market?: string | null,
): Promise<Quote> {
  const sym = normalizeSymbolParam(symbol);
  const mic = String(market ?? '').trim().toUpperCase();
  const params: Record<string, string> = { symbol: sym };
  if (mic && mic !== 'ALL') params.market = mic;
  // Conversion authority stays with /api/fx/* — no target_ccy is sent
  // (the backend ignores it; sending it only pollutes logs).
  return coalesceInflight(`quote:${sym}:${mic || 'ALL'}`, async () => {
    const { data } = await api.get('/api/market_data/quote', { params });
    return normalizeQuote(data);
  });
}

/** Freshness derivation used by FreshnessBadge.
 * Uses the EFFECTIVE age — max(expected feed delay, actual as_of age) — so
 * days-old data can never badge DELAYED when it is really STALE. */
export function freshnessOf(p: Provenance): 'live' | 'delayed' | 'stale' | 'cached' {
  if (p.fallback_used) return 'cached';
  // Negative delay is the unknown-delay sentinel (provenance absent) — stale.
  if (p.delay_minutes < 0) return 'stale';
  let effective = p.delay_minutes;
  const asOfMs = Date.parse(p.as_of);
  if (Number.isFinite(asOfMs)) {
    const ageMin = (Date.now() - asOfMs) / 60000;
    if (Number.isFinite(ageMin)) effective = Math.max(effective, ageMin);
  }
  if (effective <= 1) return 'live';
  if (effective <= 30) return 'delayed';
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
 *  backends keep working. Same signature, same Forecast return.
 *  Only 404/501 trigger the legacy fallback; other errors rethrow so
 *  ErrorState shows. Own timeout 60s (ensemble + cold serverless). */
export const FORECAST_TIMEOUT_MS = 60000;

export async function getForecast(symbol: string, horizon = 21): Promise<Forecast> {
  const sym = normalizeSymbolParam(symbol);
  const h = horizon;
  return coalesceInflight(`forecast:${sym}:${h}`, async () => {
    try {
      const { data } = await api.get(`/api/forecast/${encodeURIComponent(sym)}`, {
        params: { horizon: h },
        timeout: FORECAST_TIMEOUT_MS,
      });
      return normalizeForecast(data, sym, h);
    } catch (pathErr) {
      if (!isEndpointMissingError(pathErr)) throw pathErr;
      try {
        const { data } = await api.get('/api/forecast', {
          params: { symbol: sym, horizon: h },
          timeout: FORECAST_TIMEOUT_MS,
        });
        return normalizeForecast(data, sym, h);
      } catch {
        throw pathErr;
      }
    }
  });
}

/* ------------------------------ analytics -------------------------- */

export const AnalyticsSchema = z
  .object({
    symbol: z.string(),
    technical: z.record(z.unknown()).optional().default({}),
    fundamentals: z.record(z.unknown()).optional().default({}),
    quality: z.record(z.unknown()).optional().default({}),
    valuation: z.record(z.unknown()).optional().default({}),
    note: z.string().optional(),
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
  const noteRaw =
    (typeof r.note === 'string' ? r.note : undefined) ??
    (typeof nested.note === 'string' ? nested.note : undefined);
  const candidate = {
    symbol: (r.symbol as string | undefined) ?? (nested.symbol as string | undefined) ?? symbol,
    technical: pick('technical'),
    fundamentals: pick('fundamentals'),
    quality: pick('quality'),
    valuation: pick('valuation'),
    ...(noteRaw ? { note: noteRaw } : {}),
    provenance: normalizeProvenance({ ...nested, ...r }, 'analytics-api'),
  };
  return AnalyticsSchema.parse(candidate);
}

/** GET /api/analytics/{symbol} (path-style, backend contract).
 *  Falls back to legacy query-style /api/analytics?symbol= so older
 *  backends keep working. Same signature, same Analytics return.
 *  Only 404/501 trigger the legacy fallback; other errors rethrow.
 *  Own timeout 60s (snapshot compute + cold serverless). */
export const ANALYTICS_TIMEOUT_MS = 60000;

export async function getAnalytics(symbol: string): Promise<Analytics> {
  const sym = normalizeSymbolParam(symbol);
  return coalesceInflight(`analytics:${sym}`, async () => {
    try {
      const { data } = await api.get(`/api/analytics/${encodeURIComponent(sym)}`, {
        timeout: ANALYTICS_TIMEOUT_MS,
      });
      return normalizeAnalytics(data, sym);
    } catch (pathErr) {
      if (!isEndpointMissingError(pathErr)) throw pathErr;
      try {
        const { data } = await api.get('/api/analytics', {
          params: { symbol: sym },
          timeout: ANALYTICS_TIMEOUT_MS,
        });
        return normalizeAnalytics(data, sym);
      } catch {
        throw pathErr;
      }
    }
  });
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
 *  single-horizon Backtest view using the first requested horizon.
 *
 * Own timeout (60s, not the shared 15s): walk-forward over up to 250 bars
 * × 3 horizons exceeds 15s on cold serverless starts. */
export const BACKTEST_TIMEOUT_MS = 60000;

/** POST /api/fx/rank timeout (60s): fans out to N quotes + FX on cold starts. */
export const RANK_TIMEOUT_MS = 60000;

export async function runBacktest(symbol: string, horizons: number[]): Promise<Backtest> {
  const sym = normalizeSymbolParam(symbol);
  const h = Array.isArray(horizons) ? [...horizons] : horizons;
  return coalesceInflight(`backtest:${sym}:${JSON.stringify(h)}`, async () => {
    try {
      const { data } = await api.post(
        '/api/backtest/run',
        { symbol: sym, horizons: h },
        { timeout: BACKTEST_TIMEOUT_MS },
      );
      return normalizeBacktest(flattenBacktestRun(data, sym, h), sym, h);
    } catch (runErr) {
      // Only 404/501 (endpoint not deployed) trigger the legacy fallback;
      // other errors rethrow so ErrorState shows instead of a stale fallback.
      if (!isEndpointMissingError(runErr)) throw runErr;
      try {
        const { data } = await api.post(
          '/api/backtest',
          { symbol: sym, horizons: h },
          { timeout: BACKTEST_TIMEOUT_MS },
        );
        return normalizeBacktest(flattenBacktestRun(data, sym, h), sym, h);
      } catch {
        throw runErr;
      }
    }
  });
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
  const sym = normalizeSymbolParam(symbol);
  return coalesceInflight(`ai-insight:${sym}:${profile}`, async () => {
    const { data } = await api.post(
      '/api/ai/insight',
      { symbol: sym, profile },
      { timeout: AI_TIMEOUT_MS },
    );
    return normalizeAIOpinion(data);
  });
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
      horizon_days: (rr.horizon_days as number | undefined) ?? (rr.horizon as number | undefined),
      n_calls: rr.n_calls ?? rr.total_calls ?? rr.calls_1h ?? rr.calls ?? 0,
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
 *  Falls back to legacy GET /api/ai/performance on 404/501 only; other
 *  errors rethrow so ErrorState shows. */
export async function getAIPerformance(): Promise<AIPerformanceRow[]> {
  return coalesceInflight('ai-performance', async () => {
    try {
      const { data } = await api.get('/api/ai/providers/performance');
      return normalizeAIPerformance(data);
    } catch (pathErr) {
      if (!isEndpointMissingError(pathErr)) throw pathErr;
      try {
        const { data } = await api.get('/api/ai/performance');
        return normalizeAIPerformance(data);
      } catch {
        throw pathErr;
      }
    }
  });
}

/* ---------------------------- provider keys ------------------------ */

export type ProviderKeyStatus = {
  provider: string;
  model: string;
  configured: boolean;
  updated_at: string | null;
};

/** GET /api/providers/keys/status — config flags only, never key material. */
export async function getProviderKeysStatus(): Promise<ProviderKeyStatus[]> {
  return coalesceInflight('provider-keys-status', async () => {
    const { data } = await api.get('/api/providers/keys/status');
    const list = (data as Record<string, unknown>)?.providers;
    if (!Array.isArray(list)) return [];
    return (list as Record<string, unknown>[]).map((p) => ({
      provider: String(p.provider ?? ''),
      model: typeof p.model === 'string' ? p.model : '',
      configured: p.configured === true,
      updated_at: typeof p.updated_at === 'string' ? p.updated_at : null,
    }));
  });
}

/** GET /api/providers/budget — saved monthly caps per provider. */
export async function getProviderBudgets(): Promise<Record<string, number>> {
  return coalesceInflight('provider-budgets', async () => {
    const { data } = await api.get('/api/providers/budget');
    const budgets = (data as Record<string, unknown>)?.budgets;
    if (!budgets || typeof budgets !== 'object') return {};
    const out: Record<string, number> = {};
    for (const [k, v] of Object.entries(budgets as Record<string, unknown>)) {
      if (typeof v === 'number' && Number.isFinite(v)) out[k] = v;
    }
    return out;
  });
}

/* ---------------------------- provider health ---------------------- */

export type ProviderHealthTest = {
  ok: boolean;
  latency_ms?: number;
  message?: string;
  provider?: string;
};

/**
 * Tolerant parse of the AI health-test shape.
 * Contract (backend/api/ai.py): POST /api/ai/providers/health/test
 * {provider?} -> {providers: [{provider, model, configured, stub_mode}]}
 * (configuration only, never key material). `ok` mirrors `configured`:
 * an unconfigured provider is a FAIL with an honest "key missing"
 * message, never a silent pass.
 * Returns null when the payload carries no recognizable shape.
 */
export function normalizeAIHealthTest(raw: unknown, provider: string): ProviderHealthTest | null {
  const d = (raw ?? {}) as Record<string, unknown>;
  if (typeof d.ok === 'boolean')
    return {
      ok: d.ok,
      latency_ms: d.latency_ms as number | undefined,
      message: d.message as string | undefined,
      provider,
    };
  const list = Array.isArray(d.providers) ? (d.providers as Record<string, unknown>[]) : null;
  if (!list || list.length === 0) return null;
  const want = provider.trim().toLowerCase();
  const match =
    list.find((r) => String(r.provider ?? '').trim().toLowerCase() === want) ?? list[0];
  if (!match || typeof match !== 'object') return null;
  const name = typeof match.provider === 'string' && match.provider ? match.provider : provider;
  const model = typeof match.model === 'string' ? match.model : '';
  if (typeof match.configured === 'boolean') {
    const configured = match.configured;
    return {
      ok: configured,
      message: configured
        ? `configured${model ? ` · model ${model}` : ''}`
        : 'not configured (stub mode) — API key missing',
      provider: name,
    };
  }
  return null;
}

/**
 * AI key health probe (Providers page). Primary: POST
 * /api/ai/providers/health/test — answers whether THIS provider has a
 * usable key. Fallback: the market-data probe (quote plumbing only; can
 * never validate an AI key). Never sends or returns API keys.
 * Only 404/501 or an unrecognizable primary shape trigger the fallback;
 * other primary errors rethrow so ErrorState shows (a market-data probe
 * can never validate an AI key, so masking a 500 with "probe recorded"
 * would be dishonest).
 */
export async function testProviderHealth(
  provider: string,
): Promise<ProviderHealthTest> {
  const prov = String(provider ?? '').trim();
  try {
    const { data } = await api.post('/api/ai/providers/health/test', { provider: prov });
    const parsed = normalizeAIHealthTest(data, prov);
    if (parsed) return parsed;
    // Unrecognizable shape = version mismatch → fall through to probe below.
  } catch (err) {
    if (!isEndpointMissingError(err)) throw err;
    /* 404/501 → fall through to the market-data probe below */
  }
  const { data } = await api.post(
    '/api/providers/health/test',
    { provider: prov },
    { params: { provider: prov } },
  );
  const d = (data ?? {}) as Record<string, unknown>;
  if (typeof d.ok === 'boolean')
    return { ok: d.ok as boolean, latency_ms: d.latency_ms as number | undefined, message: d.message as string | undefined, provider: prov };
  const total = Number(d.total_calls ?? 0);
  return {
    ok: (d.circuit as string | undefined) !== 'open',
    latency_ms: d.latency_p50_ms !== undefined ? Number(d.latency_p50_ms) : undefined,
    message: total > 0 ? `probe recorded (${total} total calls)` : 'probe recorded',
    provider: (d.provider as string | undefined) ?? prov,
  };
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
  return coalesceInflight(`fx-rate:${b}:${q}`, async () => {
    const { data } = await api.get('/api/fx/rate', { params: { base: b, quote: q } });
    return normalizeFXRate(data, b, q);
  });
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
  const amt = Number(amount);
  return coalesceInflight(`fx-convert:${amt}:${f}:${t}`, async () => {
    const { data } = await api.post('/api/fx/convert', {
      amount,
      from: f,
      to: t,
    });
    return normalizeConvert(data, amount, f, t);
  });
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

export function normalizeRank(
  raw: unknown,
  symbols: string[],
  targetCcy: string,
): RankResponse {
  const r = (raw ?? {}) as Record<string, unknown>;
  // Backend canonical key first (backend/market_data/fx/convert.py sends
  // `ranked`); legacy aliases after. Missing all -> empty ranking.
  const listRaw = Array.isArray(r.ranked)
    ? r.ranked
    : Array.isArray(r.ranking)
      ? r.ranking
      : Array.isArray(r.results)
        ? r.results
        : Array.isArray(r.items)
          ? r.items
          : Array.isArray(r.rows)
            ? r.rows
            : [];
  // Tolerant: skip single bad rows instead of failing the whole rank
  // on backend shape drift (one malformed row must not void the rest).
  const ranking: RankedRow[] = [];
  for (const row of listRaw as unknown[]) {
    try {
      ranking.push(normalizeRankedRow(row, targetCcy));
    } catch {
      continue;
    }
  }
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
 * `isFreshFxProvenance(fx_provenance)` holds; on HTTP 423/502 with
 * `FX_PROVENANCE_MISSING` the error is rethrown untouched so the
 * Watchlist can render "Cross-market comparison unavailable — FX
 * provenance missing" instead of ranked numbers.
 * Symbols are normalized case-insensitively (trim, strip spaces, UPPER)
 * and deduped preserving first-seen order, so `aapl`/`AAPL` share one
 * request instead of fanning out twice. Gate errors rethrow untouched.
 */
export async function rankCrossMarket(
  symbols: string[],
  targetCcy: string,
): Promise<RankResponse> {
  const target = ccy(targetCcy, 'USD');
  const seen = new Set<string>();
  const clean: string[] = [];
  for (const s of symbols ?? []) {
    const norm = normalizeSymbolParam(s);
    if (!norm || seen.has(norm)) continue;
    seen.add(norm);
    clean.push(norm);
  }
  return coalesceInflight(`fx-rank:${clean.join(',')}:${target}`, async () => {
    const { data } = await api.post(
      '/api/fx/rank',
      {
        symbols: clean,
        target_ccy: target,
      },
      { timeout: RANK_TIMEOUT_MS },
    );
    return normalizeRank(data, clean, target);
  });
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
  return coalesceInflight('providers-health', async () => {
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
  return coalesceInflight(`audit-forecasts:${n}`, async () => {
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
  });
}

/* ------------------------------------------------------------------ */
/* Phase 3a: screener — rank the registry universe by forecast         */
/* direction probability (additive — no existing export changed).      */
/* Parsers are tolerant: alias keys accepted, stale-marked provenance  */
/* when the envelope is absent. Callers treat throw → error state.     */
/* ------------------------------------------------------------------ */

export const ScreenerRowSchema = z
  .object({
    symbol: z.string(),
    company_name: z.string().optional().default(''),
    exchange_mic: z.string().optional().default(''),
    currency: z.string().optional().default('USD'),
    price: z.number().nullable().optional(),
    change_pct: z.number().nullable().optional(),
    market_state: z.string().nullable().optional(),
    direction_probability: z.number().min(0).max(1),
    confidence: z.string().optional().default('Unknown'),
    model_version: z.string().optional().default(''),
    quality: z.record(z.unknown()).optional(),
    horizon: z.number().optional(),
    horizons: z.array(z.number()).optional().default([]),
    provenance: ProvenanceSchema,
  })
  .passthrough();
export type ScreenerRow = z.infer<typeof ScreenerRowSchema>;

export const ScreenerSkippedSchema = z
  .object({
    symbol: z.string(),
    reason: z.string().optional().default(''),
  })
  .passthrough();
export type ScreenerSkipped = z.infer<typeof ScreenerSkippedSchema>;

export const ScreenerResponseSchema = z
  .object({
    results: z.array(ScreenerRowSchema).optional().default([]),
    count: z.number().optional().default(0),
    universe_size: z.number().optional().default(0),
    skipped: z.array(ScreenerSkippedSchema).optional().default([]),
    horizon: z.number().optional(),
    disclosure: z.string().optional().default(''),
  })
  .passthrough();
export type ScreenerResponse = z.infer<typeof ScreenerResponseSchema>;

export type ScreenerParams = {
  market?: string | null;
  minDirection?: number;
  horizon?: number;
  limit?: number;
};

function normalizeScreenerRow(raw: unknown, horizon: number): ScreenerRow {
  const r = (raw ?? {}) as Record<string, unknown>;
  const prob = num(
    r.direction_probability ?? r.probability ?? r.direction_prob ?? r.proba,
    Number.NaN,
  );
  const candidate = {
    ...(r as Record<string, unknown>),
    symbol:
      (r.symbol as string | undefined) ??
      (r.ticker as string | undefined) ??
      'UNKNOWN',
    company_name:
      (r.company_name as string | undefined) ??
      (r.companyName as string | undefined) ??
      (r.name as string | undefined) ??
      '',
    exchange_mic:
      (r.exchange_mic as string | undefined) ??
      (r.exchangeMic as string | undefined) ??
      (r.mic as string | undefined) ??
      '',
    currency: (r.currency as string | undefined) ?? 'USD',
    price: rateNumber(r.price ?? r.last),
    change_pct: rateNumber(r.change_pct ?? r.changePct),
    market_state:
      (r.market_state as string | undefined) ??
      (r.marketState as string | undefined) ??
      null,
    direction_probability: prob,
    confidence: (r.confidence as string | undefined) ?? 'Unknown',
    model_version: (r.model_version as string | undefined) ?? '',
    horizon: num(r.horizon ?? horizon, horizon),
    horizons: Array.isArray(r.horizons)
      ? (r.horizons as unknown[]).map((h) => Number(h)).filter((h) => Number.isFinite(h))
      : [horizon],
    provenance: normalizeProvenance(r, 'screener-api'),
  };
  return ScreenerRowSchema.parse(candidate);
}

function normalizeScreener(raw: unknown, horizon: number): ScreenerResponse {
  const r = (raw ?? {}) as Record<string, unknown>;
  const listRaw = Array.isArray(r.results)
    ? r.results
    : Array.isArray(r.rows)
      ? r.rows
      : Array.isArray(r.items)
        ? r.items
        : [];
  // Tolerant: skip single bad rows instead of failing the whole scan
  // on backend shape drift (one row missing direction_probability must
  // not void the other N-1 rows).
  const results: ScreenerRow[] = [];
  for (const row of listRaw as unknown[]) {
    try {
      results.push(normalizeScreenerRow(row, horizon));
    } catch {
      continue;
    }
  }
  const skippedRaw = Array.isArray(r.skipped) ? (r.skipped as unknown[]) : [];
  const skipped = skippedRaw.flatMap((s) => {
    const parsed = ScreenerSkippedSchema.safeParse(s);
    return parsed.success ? [parsed.data] : [];
  });
  return ScreenerResponseSchema.parse({
    ...(r as Record<string, unknown>),
    results,
    count:
      typeof r.count === 'number'
        ? (r.count as number)
        : results.length,
    universe_size:
      typeof r.universe_size === 'number'
        ? (r.universe_size as number)
        : typeof r.universeSize === 'number'
          ? (r.universeSize as number)
          : results.length,
    skipped,
    horizon: num(r.horizon ?? horizon, horizon),
    disclosure: (r.disclosure as string | undefined) ?? '',
  });
}

/**
 * GET /api/screener?market=&min_direction=&horizon=&limit=
 * `market` accepts a MIC (XNYS/XNAS/XSHG/XPAR/XAMS/XBRU); All/empty
 * omits the param. Throws on transport/validation error so the page
 * can render its error state.
 *
 * Own timeout (60s, not the shared 15s): a full-universe scan runs a
 * quote + ensemble forecast per instrument (~30s live). Repeats are
 * faster once quotes/cache are warm.
 */
export const SCREENER_TIMEOUT_MS = 60000;

export async function getScreener(params: ScreenerParams = {}): Promise<ScreenerResponse> {
  const horizon = params.horizon ?? 21;
  const mic = String(params.market ?? '').trim().toUpperCase();
  const minDir = params.minDirection ?? 0.5;
  const lim = params.limit ?? 20;
  const query: Record<string, string | number> = {
    horizon,
    min_direction: minDir,
    limit: lim,
  };
  if (mic && mic !== 'ALL') query.market = mic;
  return coalesceInflight(`screener:${mic || 'ALL'}:${horizon}:${minDir}:${lim}`, async () => {
    const { data } = await api.get('/api/screener', {
      params: query,
      timeout: SCREENER_TIMEOUT_MS,
    });
    return normalizeScreener(data, horizon);
  });
}

/* ------------------------------------------------------------------ */
/* Price history: GET /api/market_data/bars (additive).                 */
/* Backend contract (backend/api/market_data.py + schemas.py):          */
/*   GET /api/market_data/bars?symbol=AAPL&timeframe=1d&limit=30        */
/*   -> { symbol, instrument_id?, timeframe,                            */
/*        bars: [{ ts, open, high, low, close, volume?, missing_fields }],*/
/*        provenance }                                                  */
/* The parser is tolerant (alias keys accepted, invalid rows dropped,   */
/* stale-marked provenance when the envelope is absent) but never       */
/* invents candles: unparseable/empty payloads yield zero candles and   */
/* callers render an honest unavailable state.                          */
/* ------------------------------------------------------------------ */

export const BarSchema = z
  .object({
    ts: z.string(),
    open: z.number().nullable().optional(),
    high: z.number().nullable().optional(),
    low: z.number().nullable().optional(),
    close: z.number().nullable().optional(),
    volume: z.number().nullable().optional(),
  })
  .passthrough();
export type Bar = z.infer<typeof BarSchema>;

export const BarsResponseSchema = z
  .object({
    symbol: z.string(),
    instrument_id: z.string().nullable().optional(),
    timeframe: z.string().optional().default('1d'),
    bars: z.array(BarSchema).optional().default([]),
    provenance: ProvenanceSchema,
  })
  .passthrough();
export type BarsResponse = z.infer<typeof BarsResponseSchema>;

/** Chart-ready candle (structurally matches PriceChart's Candle). */
export type BarsCandle = {
  time: string; // YYYY-MM-DD
  open: number;
  high: number;
  low: number;
  close: number;
};

export type BarsData = {
  symbol: string;
  timeframe: string;
  candles: BarsCandle[];
  provenance: Provenance;
};

function numFinite(v: unknown): number | null {
  if (v === null || v === undefined || v === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

/** ts/time/date (ISO string or epoch) -> YYYY-MM-DD. Null when unparseable. */
export function normalizeBarTime(v: unknown): string | null {
  if (typeof v === 'number' && Number.isFinite(v)) {
    // Heuristic: seconds (< 1e12) vs milliseconds.
    const ms = Math.abs(v) < 1e12 ? v * 1000 : v;
    const d = new Date(ms);
    return Number.isNaN(d.getTime()) ? null : d.toISOString().slice(0, 10);
  }
  if (typeof v === 'string') {
    const s = v.trim();
    if (!s) return null;
    const day = s.slice(0, 10);
    if (/^\d{4}-\d{2}-\d{2}$/.test(day)) return day;
    const d = new Date(s);
    return Number.isNaN(d.getTime()) ? null : d.toISOString().slice(0, 10);
  }
  return null;
}

/**
 * Tolerant bars normalization (pure — safe to unit-test without network).
 * Rows with an unparseable timestamp or any non-finite OHLC value are
 * dropped, never zero-filled. Provenance falls back to a stale-marked
 * envelope so badges render CACHED/FALLBACK instead of fake LIVE.
 */
export function normalizeBarsToCandles(
  raw: unknown,
  symbol: string,
  timeframe = '1d',
): BarsData {
  const r = (raw ?? {}) as Record<string, unknown>;
  const listRaw = Array.isArray(r.bars)
    ? (r.bars as unknown[])
    : Array.isArray(r.data)
      ? (r.data as unknown[])
      : Array.isArray(r.candles)
        ? (r.candles as unknown[])
        : [];
  const candles: BarsCandle[] = [];
  for (const row of listRaw) {
    if (!row || typeof row !== 'object') continue;
    const rec = row as Record<string, unknown>;
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
    symbol:
      (r.symbol as string | undefined) ??
      (r.ticker as string | undefined) ??
      symbol,
    timeframe: (r.timeframe as string | undefined) ?? timeframe,
    candles,
    provenance: normalizeProvenance(r, 'bars-api'),
  };
}

/**
 * GET /api/market_data/bars?symbol=&timeframe=1d&limit=
 * Throws on transport/validation error so callers render loading/error/
 * unavailable states. Never synthesizes candles client-side.
 */
export async function getBars(
  symbol: string,
  timeframe = '1d',
  limit = 90,
): Promise<BarsData> {
  const sym = normalizeSymbolParam(symbol);
  const tf = String(timeframe ?? '1d').trim() || '1d';
  const n = Number.isFinite(Number(limit))
    ? Math.min(250, Math.max(1, Math.floor(Number(limit))))
    : 90;
  return coalesceInflight(`bars:${sym}:${tf}:${n}`, async () => {
    const { data } = await api.get('/api/market_data/bars', {
      params: { symbol: sym, timeframe: tf, limit: n },
    });
    return normalizeBarsToCandles(data, sym, tf);
  });
}
