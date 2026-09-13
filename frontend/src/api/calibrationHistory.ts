import { api, type ReliabilityRow } from './client';

export type CalibrationHistoryEntry = {
  brier: number | null;
  ece: number | null;
  n_windows: number | null;
  members: Record<string, unknown> | null;
  reliability: ReliabilityRow[];
  model_version?: string | null;
  data_version?: string | null;
  created_at?: string | null;
};

function numOrNull(v: unknown): number | null {
  if (v === null || v === undefined || v === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function normalizeReliability(v: unknown): ReliabilityRow[] {
  if (!Array.isArray(v)) return [];
  const out: ReliabilityRow[] = [];
  for (const row of v) {
    if (!row || typeof row !== 'object') continue;
    const r = row as Record<string, unknown>;
    const binLow = Number(r.bin_low ?? r.low);
    const binHigh = Number(r.bin_high ?? r.high);
    const count = Number(r.count ?? r.n);
    if (!Number.isFinite(binLow) || !Number.isFinite(binHigh) || !Number.isFinite(count)) {
      continue;
    }
    const meanRaw = r.mean_predicted ?? r.meanPredicted ?? r.pred;
    const fracRaw = r.fraction_positive ?? r.fractionPositive ?? r.obs;
    out.push({
      bin_low: binLow,
      bin_high: binHigh,
      count,
      mean_predicted:
        meanRaw === null || meanRaw === undefined || meanRaw === ''
          ? null
          : Number(meanRaw),
      fraction_positive:
        fracRaw === null || fracRaw === undefined || fracRaw === ''
          ? null
          : Number(fracRaw),
    } as ReliabilityRow);
  }
  return out;
}

function normalizeEntry(raw: unknown): CalibrationHistoryEntry | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  const meta =
    (r.calibration_meta as Record<string, unknown> | undefined) ??
    (r.meta as Record<string, unknown> | undefined) ??
    r;
  const reliability = normalizeReliability(
    r.reliability ?? r.calibration ?? r.rows ?? r.bins ?? [],
  );
  const membersRaw = r.members ?? r.member_accuracy ?? meta.members ?? null;
  const members =
    membersRaw && typeof membersRaw === 'object' && !Array.isArray(membersRaw)
      ? (membersRaw as Record<string, unknown>)
      : null;
  const brier = numOrNull(r.brier ?? r.brier_score ?? meta.brier ?? meta.brier_score);
  const ece = numOrNull(
    r.ece ?? r.calibration_error ?? r.ece_score ?? meta.ece ?? meta.calibration_error,
  );
  const nWindows = numOrNull(
    r.n_windows ?? r.nWindows ?? r.n_points ?? r.n ?? meta.n_windows ?? meta.n_points,
  );
  const modelVersion =
    (typeof r.model_version === 'string' ? (r.model_version as string) : null) ??
    (typeof meta.model_version === 'string' ? (meta.model_version as string) : null);
  const dataVersion =
    (typeof r.data_version === 'string' ? (r.data_version as string) : null) ??
    (typeof meta.data_version === 'string' ? (meta.data_version as string) : null);
  const createdAt =
    (typeof r.created_at === 'string' ? (r.created_at as string) : null) ??
    (typeof r.as_of === 'string' ? (r.as_of as string) : null) ??
    (typeof meta.created_at === 'string' ? (meta.created_at as string) : null) ??
    (typeof meta.as_of === 'string' ? (meta.as_of as string) : null);
  // Skip fully-empty rows (no scores, no bins, no versions).
  if (
    brier === null &&
    ece === null &&
    nWindows === null &&
    reliability.length === 0 &&
    !modelVersion &&
    !dataVersion
  ) {
    return null;
  }
  return {
    brier,
    ece,
    n_windows: nWindows,
    members,
    reliability,
    model_version: modelVersion,
    data_version: dataVersion,
    created_at: createdAt,
  };
}

function extractList(data: unknown): unknown[] {
  if (Array.isArray(data)) return data;
  if (!data || typeof data !== 'object') return [];
  const r = data as Record<string, unknown>;
  for (const key of ['history', 'snapshots', 'rows', 'results', 'runs', 'items']) {
    if (Array.isArray(r[key])) return r[key] as unknown[];
  }
  return [];
}

/**
 * GET /api/forecast/{symbol}/calibration/history?horizon=&limit= with
 * fallback to the embedded `calibration` array on GET
 * /api/forecast/{symbol}?horizon=. Always normalizes to
 * {brier,ece,n_windows,members,reliability,model_version,data_version,created_at}[].
 * Missing endpoint / empty history → [] (callers render "No calibration bins yet").
 */
export async function getCalibrationHistory(
  symbol: string,
  horizon: number,
  limit = 20,
): Promise<CalibrationHistoryEntry[]> {
  const sym = String(symbol ?? '').trim();
  const h = Number(horizon);
  if (!sym || !Number.isFinite(h)) return [];
  const lim = Number.isFinite(Number(limit))
    ? Math.min(100, Math.max(1, Number(limit)))
    : 20;

  // Primary: dedicated calibration-history endpoint.
  try {
    const { data } = await api.get(
      `/api/forecast/${encodeURIComponent(sym)}/calibration/history`,
      { params: { horizon: h, limit: lim } },
    );
    const out: CalibrationHistoryEntry[] = [];
    for (const row of extractList(data)) {
      const parsed = normalizeEntry(row);
      if (parsed) out.push(parsed);
    }
    if (out.length > 0) return out.slice(0, lim);
    // Empty history is a valid answer — fall through to the embedded
    // forecast calibration so the latest snapshot still shows.
  } catch {
    /* endpoint missing (404) or unreachable — use the forecast fallback below */
  }

  // Fallback: embedded calibration on the forecast payload.
  try {
    const { data } = await api.get(`/api/forecast/${encodeURIComponent(sym)}`, {
      params: { horizon: h },
    });
    const r = (data ?? {}) as Record<string, unknown>;
    const meta =
      (r.calibration_meta as Record<string, unknown> | undefined) ??
      (r.calibrationMeta as Record<string, unknown> | undefined);
    const versions = (r.versions as Record<string, unknown> | undefined) ?? {};
    const inputs = (r.inputs as Record<string, unknown> | undefined) ?? {};
    const provenance = (r.provenance as Record<string, unknown> | undefined) ?? {};
    const single = normalizeEntry({
      brier: r.brier ?? r.brier_score ?? meta?.brier ?? meta?.brier_score ?? null,
      ece: r.ece ?? r.calibration_error ?? meta?.ece ?? meta?.calibration_error ?? null,
      n_windows:
        r.n_windows ??
        inputs.n_windows ??
        (r.expected_return_range as Record<string, unknown> | undefined)?.n_windows ??
        meta?.n_windows ??
        null,
      members: r.members ?? null,
      reliability: r.calibration ?? r.reliability ?? [],
      model_version:
        r.model_version ?? versions.model_version ?? inputs.model_version ?? meta?.model_version ?? null,
      data_version:
        r.data_version ?? versions.data_version ?? inputs.data_version ?? meta?.data_version ?? null,
      created_at: provenance.as_of ?? meta?.created_at ?? meta?.as_of ?? null,
    });
    return single ? [single] : [];
  } catch {
    return [];
  }
}
