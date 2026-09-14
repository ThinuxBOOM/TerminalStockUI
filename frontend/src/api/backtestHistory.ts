import { api, coalesceInflight, normalizeSymbolParam, type ReliabilityRow } from './client';

export const BACKTEST_RECENT_KEY = 'onemarket.backtest.recent.v1';
const MAX_RECENT = 20;

export type BacktestHistoryMetrics = {
  n_folds?: number | null;
  n_points?: number | null;
  brier?: number | null;
  ece?: number | null;
};

export type BacktestHistoryRun = {
  run_id: string;
  as_of?: string | null;
  horizons: number[];
  params?: Record<string, unknown>;
  metrics: Record<string, BacktestHistoryMetrics>;
  model_version?: string | null;
  feature_version?: string | null;
  data_version?: string | null;
  reliability?: ReliabilityRow[];
};

export type RecentBacktest = {
  symbol: string;
  horizons: number[];
  at: string;
};

function numOrNull(v: unknown): number | null {
  if (v === null || v === undefined || v === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function strOrUndef(v: unknown): string | undefined {
  return typeof v === 'string' && v ? v : undefined;
}

function normalizeRun(raw: unknown): BacktestHistoryRun | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  const runId = strOrUndef(r.run_id ?? r.runId ?? r.id);
  if (!runId) return null;
  const horizonsRaw = Array.isArray(r.horizons) ? (r.horizons as unknown[]) : [];
  const horizons = horizonsRaw
    .map((h) => Number(h))
    .filter((n) => Number.isFinite(n));
  const metricsRaw =
    (r.metrics as Record<string, unknown> | undefined) ??
    (r.results as Record<string, unknown> | undefined) ??
    {};
  const metrics: Record<string, BacktestHistoryMetrics> = {};
  if (metricsRaw && typeof metricsRaw === 'object' && !Array.isArray(metricsRaw)) {
    for (const [k, v] of Object.entries(metricsRaw)) {
      const m = (v ?? {}) as Record<string, unknown>;
      metrics[String(k)] = {
        n_folds: m.n_folds !== undefined ? numOrNull(m.n_folds ?? m.nFolds) : null,
        n_points: m.n_points !== undefined ? numOrNull(m.n_points ?? m.nPoints ?? m.n) : numOrNull(m.n_points ?? m.n),
        brier: m.brier !== undefined ? numOrNull(m.brier ?? m.brier_score) : numOrNull(m.brier ?? m.brier_score),
        ece: m.ece !== undefined ? numOrNull(m.ece ?? m.calibration_error) : numOrNull(m.ece ?? m.calibration_error),
      };
    }
  }
  const reliabilityRaw = r.reliability ?? r.reliability_table ?? r.table;
  const reliability = Array.isArray(reliabilityRaw)
    ? (reliabilityRaw as ReliabilityRow[])
    : undefined;
  const params =
    r.params && typeof r.params === 'object' && !Array.isArray(r.params)
      ? (r.params as Record<string, unknown>)
      : undefined;
  return {
    run_id: runId,
    as_of: typeof r.as_of === 'string' ? (r.as_of as string) : null,
    horizons,
    params,
    metrics,
    model_version: typeof r.model_version === 'string' ? (r.model_version as string) : null,
    feature_version: typeof r.feature_version === 'string' ? (r.feature_version as string) : null,
    data_version: typeof r.data_version === 'string' ? (r.data_version as string) : null,
    ...(reliability ? { reliability } : {}),
  };
}

/**
 * GET /api/backtest/{symbol}?include_reliability=true — lightweight
 * per-symbol run history (summaries; backend omits full reliability
 * tables). Tolerant: unknown shapes → []. Only 404/501 (never run /
 * not deployed) → []; other transport errors rethrow so ErrorState
 * shows instead of a silent empty list. Shares the TanStack
 * ['backtest-history', SYMBOL] resource via in-flight coalescing for
 * divergent call sites; symbol key is case-insensitive (UPPER).
 */
export async function getBacktestHistory(
  symbol: string,
  includeReliability = true,
): Promise<BacktestHistoryRun[]> {
  const sym = normalizeSymbolParam(symbol);
  if (!sym) return [];
  return coalesceInflight(`backtest-history:${sym}:${includeReliability}`, async () => {
    try {
      const { data } = await api.get(`/api/backtest/${encodeURIComponent(sym)}`, {
        params: { include_reliability: includeReliability },
      });
      const raw = (data ?? {}) as Record<string, unknown>;
      const listRaw = Array.isArray(data)
        ? (data as unknown[])
        : Array.isArray(raw.runs)
          ? (raw.runs as unknown[])
          : Array.isArray(raw.results)
            ? (raw.results as unknown[])
            : Array.isArray(raw.history)
              ? (raw.history as unknown[])
              : [];
      const out: BacktestHistoryRun[] = [];
      for (const row of listRaw) {
        const parsed = normalizeRun(row);
        if (parsed) out.push(parsed);
      }
      return out;
    } catch (err) {
      const status = (err as { response?: { status?: number } })?.response?.status;
      if (status === 404 || status === 501) return [];
      throw err;
    }
  });
}

/** Append {symbol, horizons, at} to the localStorage recent list (max 20). */
export function saveRecentBacktest(symbol: string, horizons: number[]): void {
  try {
    if (typeof localStorage === 'undefined') return;
    const sym = normalizeSymbolParam(symbol);
    if (!sym) return;
    const clean = horizons
      .map((h) => Number(h))
      .filter((n) => Number.isFinite(n));
    const entry: RecentBacktest = {
      symbol: sym,
      horizons: clean,
      at: new Date().toISOString(),
    };
    const prev = getRecentBacktests();
    const next = [entry, ...prev.filter((r) => r.symbol !== sym)].slice(0, MAX_RECENT);
    localStorage.setItem(BACKTEST_RECENT_KEY, JSON.stringify(next));
  } catch {
    /* storage unavailable — recent list stays in-memory */
  }
}

/** Tolerant reader for the localStorage recent-backtests list. */
export function getRecentBacktests(): RecentBacktest[] {
  try {
    if (typeof localStorage === 'undefined') return [];
    const raw = localStorage.getItem(BACKTEST_RECENT_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    const out: RecentBacktest[] = [];
    for (const row of parsed) {
      if (!row || typeof row !== 'object') continue;
      const r = row as Record<string, unknown>;
      const symbol = normalizeSymbolParam(r.symbol);
      if (!symbol) continue;
      const horizons = Array.isArray(r.horizons)
        ? (r.horizons as unknown[]).map((h) => Number(h)).filter((n) => Number.isFinite(n))
        : [];
      const at = typeof r.at === 'string' ? r.at : new Date(0).toISOString();
      out.push({ symbol, horizons, at });
    }
    return out.slice(0, MAX_RECENT);
  } catch {
    return [];
  }
}
