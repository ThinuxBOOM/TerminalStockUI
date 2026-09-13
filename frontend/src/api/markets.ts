import { z } from 'zod';
import {
  api,
  getQuote,
  getScreener,
  ProvenanceSchema,
  SUPPORTED_MARKET_MICS,
  type Provenance,
  type ScreenerRow,
} from './client';

/* ------------------------------------------------------------------ */
/* Per-market liquidity + breadth (homepage).                           */
/* Primary: GET /api/markets/overview and GET /api/markets/{mic}/      */
/* liquidity (tolerant parsing). Fallback: those endpoints are not      */
/* deployed yet, so on 404/501 fan out client-side via the existing    */
/* getScreener({market:mic}) + getQuote enrichment and compute          */
/* advancers/decliners/avg_change/total_volume locally. The fallback    */
/* is always marked fallback_used + grade D so the UI gates it         */
/* honestly (never ranked, never shown as live). Never throws for a     */
/* missing endpoint — only when every market fails.                     */
/* ------------------------------------------------------------------ */

export const MARKET_MICS: string[] = [...SUPPORTED_MARKET_MICS];

export const MARKET_LABELS: Record<string, string> = {
  XNYS: 'NYSE (XNYS)',
  XNAS: 'NASDAQ (XNAS)',
  XSHG: 'SSE (XSHG)',
  XPAR: 'Euronext Paris (XPAR)',
  XAMS: 'Euronext Amsterdam (XAMS)',
  XBRU: 'Euronext Brussels (XBRU)',
};

export type MarketBreadth = {
  mic: string;
  label: string;
  advancers: number;
  decliners: number;
  unchanged: number;
  total: number;
  avg_change_pct: number | null;
  total_volume: number | null;
  turnover: number | null;
  avg_range_pct: number | null;
  market_state_counts: Record<string, number>;
  provenance: Provenance;
};

export type MarketsOverview = {
  markets: MarketBreadth[];
  provenance: Provenance;
  fallback_used: boolean;
};

/** Tolerant schema for a backend-served per-market row (aliases accepted by the normalizer). */
export const MarketBreadthSchema = z
  .object({
    mic: z.string(),
    label: z.string().optional().default(''),
    advancers: z.number().optional().default(0),
    decliners: z.number().optional().default(0),
    unchanged: z.number().optional().default(0),
    total: z.number().optional().default(0),
    avg_change_pct: z.number().nullable().optional(),
    total_volume: z.number().nullable().optional(),
    turnover: z.number().nullable().optional(),
    avg_range_pct: z.number().nullable().optional(),
    market_state_counts: z.record(z.number()).optional().default({}),
    provenance: ProvenanceSchema,
  })
  .passthrough();
export type MarketBreadthParsed = z.infer<typeof MarketBreadthSchema>;

/** Stale gate: fallback data or grade D is never ranked, only shown with an honest badge. */
export function isStaleLiquidity(p: Provenance | null | undefined): boolean {
  if (!p || typeof p !== 'object') return true;
  if (p.fallback_used) return true;
  return String(p.quality_grade ?? '').trim().toUpperCase() === 'D';
}

/* ------------------------- tolerant primitives ------------------------ */

function isRecord(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === 'object' && !Array.isArray(v);
}

function numOrNull(v: unknown): number | null {
  if (v === null || v === undefined || v === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function numOrZero(v: unknown): number {
  const n = Number(v);
  return Number.isFinite(n) && n >= 0 ? Math.floor(n) : 0;
}

function str(v: unknown, fallback = ''): string {
  return typeof v === 'string' && v.trim() !== '' ? v : fallback;
}

/** Local provenance normalizer (client.ts copy is private — same tolerant semantics). */
function localProvenance(raw: unknown, sourceFallback: string): Provenance {
  const r = isRecord(raw) ? raw : {};
  const nested = isRecord(r.meta) ? (r.meta.provenance as unknown) : undefined;
  const cand = (r.provenance as unknown) ?? nested;
  if (isRecord(cand)) {
    const parsed = ProvenanceSchema.passthrough().safeParse(cand);
    if (parsed.success) return parsed.data;
  }
  const delayRaw = r.delay_minutes ?? (isRecord(cand) ? cand.delay_minutes : undefined);
  return {
    source: str(r.source, sourceFallback),
    as_of: str(r.as_of, new Date().toISOString()),
    delay_minutes: typeof delayRaw === 'number' ? Number(delayRaw) : -1,
    quality_grade: str(
      (r.quality_grade as unknown) ?? (r.data_quality as unknown) ?? (r.grade as unknown),
      'U',
    ),
    fallback_used: true,
    missing_fields: ['provenance'],
  };
}

function httpStatus(err: unknown): number | null {
  const e = err as { response?: { status?: unknown }; status?: unknown } | null;
  const s = e?.response?.status ?? e?.status;
  return typeof s === 'number' ? s : null;
}

/** 404/501 = endpoint not yet deployed → use the client-side fallback. Other errors rethrow. */
function isEndpointMissingError(err: unknown): boolean {
  const s = httpStatus(err);
  return s === 404 || s === 501;
}

/* ------------------------- backend-shape parsing ---------------------- */

/** Tolerant parse of one backend market row (alias keys accepted, never throws). */
export function normalizeMarketBreadth(raw: unknown, micFallback = ''): MarketBreadth {
  const r = isRecord(raw) ? raw : {};
  const mic = str(
    r.mic ?? r.exchange_mic ?? r.market,
    micFallback || 'UNKNOWN',
  ).toUpperCase();
  const advancers = numOrZero(r.advancers ?? r.adv ?? r.up ?? r.advancing);
  const decliners = numOrZero(r.decliners ?? r.dec ?? r.down ?? r.declining);
  const unchanged = numOrZero(r.unchanged ?? r.unch ?? r.flat ?? r.unchanged_count);
  const totalRaw = numOrZero(r.total ?? r.count ?? r.universe ?? r.universe_size);
  const total = totalRaw > 0 ? totalRaw : advancers + decliners + unchanged;
  const countsRaw = isRecord(r.market_state_counts)
    ? r.market_state_counts
    : isRecord(r.state_counts)
      ? r.state_counts
      : isRecord(r.by_state)
        ? r.by_state
        : {};
  const market_state_counts: Record<string, number> = {};
  for (const [k, v] of Object.entries(countsRaw)) {
    const n = Number(v);
    if (Number.isFinite(n) && n > 0) market_state_counts[String(k)] = Math.floor(n);
  }
  return {
    mic,
    label: str(r.label ?? r.name, MARKET_LABELS[mic] ?? mic),
    advancers,
    decliners,
    unchanged,
    total,
    avg_change_pct: numOrNull(
      r.avg_change_pct ?? r.avgChangePct ?? r.avg_change ?? r.mean_change_pct,
    ),
    total_volume: numOrNull(
      r.total_volume ?? r.volume ?? r.totalVolume ?? r.volume_shares,
    ),
    turnover: numOrNull(
      r.turnover ?? r.notional ?? r.turnover_value ?? r.total_turnover,
    ),
    avg_range_pct: numOrNull(
      r.avg_range_pct ?? r.avgRangePct ?? r.avg_range ?? r.mean_range_pct,
    ),
    market_state_counts,
    provenance: localProvenance(r, `markets-api:${mic}`),
  };
}

/** Tolerant parse of GET /api/markets/overview (object with `markets` or a bare array). */
export function normalizeMarketsOverview(raw: unknown): MarketsOverview {
  const r = isRecord(raw) ? raw : {};
  const listRaw = Array.isArray(raw)
    ? (raw as unknown[])
    : Array.isArray(r.markets)
      ? (r.markets as unknown[])
      : Array.isArray(r.data)
        ? (r.data as unknown[])
        : Array.isArray(r.results)
          ? (r.results as unknown[])
          : Array.isArray(r.items)
            ? (r.items as unknown[])
            : [];
  const markets = listRaw.map((row, i) =>
    normalizeMarketBreadth(row, MARKET_MICS[i] ?? ''),
  );
  const provenance = localProvenance(raw, 'markets-api');
  const explicitFallback =
    typeof r.fallback_used === 'boolean' ? (r.fallback_used as boolean) : false;
  return {
    markets,
    provenance,
    fallback_used:
      explicitFallback ||
      provenance.fallback_used ||
      markets.some((m) => m.provenance.fallback_used),
  };
}

/* ------------------------- client-side fallback ----------------------- */

function rowVolume(row: ScreenerRow): number | null {
  const r = row as ScreenerRow & Record<string, unknown>;
  return numOrNull(r.volume ?? r.total_volume ?? r.volume_shares ?? r.shares_volume);
}

function rowRangePct(row: ScreenerRow): number | null {
  const r = row as ScreenerRow & Record<string, unknown>;
  return numOrNull(r.range_pct ?? r.rangePct ?? r.day_range_pct ?? r.avg_range_pct);
}

/** Pure breadth computation from screener rows (unit-testable, never throws). */
export function computeBreadthFromScreener(mic: string, rows: ScreenerRow[]): MarketBreadth {
  const upper = mic.trim().toUpperCase();
  let advancers = 0;
  let decliners = 0;
  let unchanged = 0;
  let changeSum = 0;
  let changeN = 0;
  let volSum = 0;
  let volN = 0;
  let turnoverSum = 0;
  let turnoverN = 0;
  let rangeSum = 0;
  let rangeN = 0;
  const market_state_counts: Record<string, number> = {};
  const rowMissing = new Set<string>();
  for (const row of rows) {
    const cp = numOrNull(row.change_pct);
    if (cp === null) unchanged += 1;
    else if (cp > 0) advancers += 1;
    else if (cp < 0) decliners += 1;
    else unchanged += 1;
    if (cp !== null) {
      changeSum += cp;
      changeN += 1;
    }
    const st =
      typeof row.market_state === 'string' && row.market_state ? row.market_state : 'unknown';
    market_state_counts[st] = (market_state_counts[st] ?? 0) + 1;
    const vol = rowVolume(row);
    if (vol !== null) {
      volSum += vol;
      volN += 1;
    }
    const px = numOrNull(row.price);
    if (vol !== null && px !== null) {
      turnoverSum += vol * px;
      turnoverN += 1;
    }
    const rg = rowRangePct(row);
    if (rg !== null) {
      rangeSum += rg;
      rangeN += 1;
    }
    for (const m of row.provenance?.missing_fields ?? []) rowMissing.add(String(m));
  }
  const missing = new Set<string>(['markets-overview-endpoint']);
  if (volN === 0) missing.add('volume');
  if (turnoverN === 0) missing.add('turnover');
  if (rangeN === 0) missing.add('avg_range_pct');
  if (rows.length > 0 && changeN === 0) missing.add('change_pct');
  for (const m of rowMissing) missing.add(m);
  return {
    mic: upper,
    label: MARKET_LABELS[upper] ?? upper,
    advancers,
    decliners,
    unchanged,
    total: rows.length,
    avg_change_pct: changeN > 0 ? changeSum / changeN : null,
    total_volume: volN > 0 ? volSum : null,
    turnover: turnoverN > 0 ? turnoverSum : null,
    avg_range_pct: rangeN > 0 ? rangeSum / rangeN : null,
    market_state_counts,
    provenance: {
      source: `client-fallback:screener${rows.length === 0 ? ':empty' : ''}`,
      as_of: new Date().toISOString(),
      delay_minutes: -1,
      quality_grade: 'D',
      fallback_used: true,
      missing_fields: [...missing],
    },
  };
}

/** Max per-market quote enrichments (bounded fan-out, failures ignored). */
const QUOTE_ENRICH_CAP = 12;

/**
 * Fill rows missing change_pct via getQuote (best-effort, never throws).
 * Screener rows carry change_pct already; this only patches gaps so the
 * advancers/decliners split stays honest instead of lumping unknowns.
 */
async function enrichWithQuotes(rows: ScreenerRow[]): Promise<void> {
  const targets = rows
    .filter((r) => numOrNull(r.change_pct) === null)
    .slice(0, QUOTE_ENRICH_CAP);
  if (targets.length === 0) return;
  const settled = await Promise.allSettled(targets.map((r) => getQuote(r.symbol)));
  const bySymbol = new Map<string, { change_pct: unknown; market_state: unknown }>();
  settled.forEach((s, i) => {
    if (s.status !== 'fulfilled') return;
    bySymbol.set(targets[i].symbol.toUpperCase(), {
      change_pct: s.value.change_pct,
      market_state: s.value.market_state,
    });
  });
  for (const row of rows) {
    if (numOrNull(row.change_pct) !== null) continue;
    const hit = bySymbol.get(row.symbol.toUpperCase());
    if (!hit) continue;
    const rec = row as unknown as Record<string, unknown>;
    if (numOrNull(hit.change_pct) !== null) rec.change_pct = Number(hit.change_pct);
    if (!row.market_state && typeof hit.market_state === 'string') {
      rec.market_state = hit.market_state;
    }
  }
}

async function getMarketLiquidityFallback(mic: string): Promise<MarketBreadth> {
  const upper = mic.trim().toUpperCase();
  const screen = await getScreener({ market: upper, minDirection: 0, limit: 50 });
  const rows = [...screen.results];
  await enrichWithQuotes(rows);
  return computeBreadthFromScreener(upper, rows);
}

async function getMarketsOverviewFallback(): Promise<MarketsOverview> {
  const settled = await Promise.allSettled(
    MARKET_MICS.map((mic) => getMarketLiquidityFallback(mic)),
  );
  const markets: MarketBreadth[] = [];
  let firstError: unknown = new Error('screener fallback failed for every market');
  let sawError = false;
  for (const s of settled) {
    if (s.status === 'fulfilled') markets.push(s.value);
    else if (!sawError) {
      sawError = true;
      firstError = s.reason;
    }
  }
  if (markets.length === 0) throw firstError;
  const missing = new Set<string>(['markets-overview-endpoint']);
  for (const m of markets) for (const f of m.provenance.missing_fields) missing.add(f);
  return {
    markets,
    provenance: {
      source: 'client-fallback:screener+quote',
      as_of: new Date().toISOString(),
      delay_minutes: -1,
      quality_grade: 'D',
      fallback_used: true,
      missing_fields: [...missing],
    },
    fallback_used: true,
  };
}

/* ------------------------------ entrypoints --------------------------- */

/**
 * GET /api/markets/overview → per-market liquidity+breadth.
 * Falls back to client-side screener fan-out when the endpoint is not
 * deployed (404/501). Rethrows other errors so the page can render its
 * error state. Never crashes on unparseable payloads (tolerant parse).
 */
export async function getMarketsOverview(): Promise<MarketsOverview> {
  try {
    const { data } = await api.get('/api/markets/overview', { timeout: 60000 });
    return normalizeMarketsOverview(data);
  } catch (err) {
    if (!isEndpointMissingError(err)) throw err;
    return getMarketsOverviewFallback();
  }
}

/**
 * GET /api/markets/{mic}/liquidity → single-market breadth.
 * Same 404/501 → screener-fallback contract as the overview.
 */
export async function getMarketLiquidity(mic: string): Promise<MarketBreadth> {
  const upper = mic.trim().toUpperCase();
  try {
    const { data } = await api.get(`/api/markets/${encodeURIComponent(upper)}/liquidity`, {
      timeout: 60000,
    });
    return normalizeMarketBreadth(data, upper);
  } catch (err) {
    if (!isEndpointMissingError(err)) throw err;
    return getMarketLiquidityFallback(upper);
  }
}
