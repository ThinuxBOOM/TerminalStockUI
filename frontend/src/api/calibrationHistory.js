import { api, coalesceInflight, FORECAST_TIMEOUT_MS, normalizeSymbolParam } from "./client";
function numOrNull(v) {
  if (v === null || v === void 0 || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}
function normalizeReliability(v) {
  if (!Array.isArray(v)) return [];
  const out = [];
  for (const row of v) {
    if (!row || typeof row !== "object") continue;
    const r = row;
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
      mean_predicted: meanRaw === null || meanRaw === void 0 || meanRaw === "" ? null : Number(meanRaw),
      fraction_positive: fracRaw === null || fracRaw === void 0 || fracRaw === "" ? null : Number(fracRaw)
    });
  }
  return out;
}
function normalizeEntry(raw) {
  if (!raw || typeof raw !== "object") return null;
  const r = raw;
  const meta = r.calibration_meta ?? r.meta ?? r;
  const reliability = normalizeReliability(
    r.reliability ?? r.calibration ?? r.rows ?? r.bins ?? []
  );
  const membersRaw = r.members ?? r.member_accuracy ?? meta.members ?? null;
  const members = membersRaw && typeof membersRaw === "object" && !Array.isArray(membersRaw) ? membersRaw : null;
  const brier = numOrNull(r.brier ?? r.brier_score ?? meta.brier ?? meta.brier_score);
  const ece = numOrNull(
    r.ece ?? r.calibration_error ?? r.ece_score ?? meta.ece ?? meta.calibration_error
  );
  const nWindows = numOrNull(
    r.n_windows ?? r.nWindows ?? r.n_points ?? r.n ?? meta.n_windows ?? meta.n_points
  );
  const modelVersion = (typeof r.model_version === "string" ? r.model_version : null) ?? (typeof meta.model_version === "string" ? meta.model_version : null);
  const dataVersion = (typeof r.data_version === "string" ? r.data_version : null) ?? (typeof meta.data_version === "string" ? meta.data_version : null);
  const createdAt = (typeof r.created_at === "string" ? r.created_at : null) ?? (typeof r.as_of === "string" ? r.as_of : null) ?? (typeof meta.created_at === "string" ? meta.created_at : null) ?? (typeof meta.as_of === "string" ? meta.as_of : null);
  if (brier === null && ece === null && nWindows === null && reliability.length === 0 && !modelVersion && !dataVersion) {
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
    created_at: createdAt
  };
}
function extractList(data) {
  if (Array.isArray(data)) return data;
  if (!data || typeof data !== "object") return [];
  const r = data;
  for (const key of ["history", "snapshots", "rows", "results", "runs", "items"]) {
    if (Array.isArray(r[key])) return r[key];
  }
  return [];
}
async function getCalibrationHistory(symbol, horizon, limit = 20) {
  const sym = normalizeSymbolParam(symbol);
  const h = Number(horizon);
  if (!sym || !Number.isFinite(h)) return [];
  const lim = Number.isFinite(Number(limit)) ? Math.min(100, Math.max(1, Number(limit))) : 20;
  return coalesceInflight(`calibration-history:${sym}:${h}:${lim}`, async () => {
    try {
      const { data } = await api.get(
        `/api/forecast/${encodeURIComponent(sym)}/calibration/history`,
        { params: { horizon: h, limit: lim } }
      );
      const out = [];
      for (const row of extractList(data)) {
        const parsed = normalizeEntry(row);
        if (parsed) out.push(parsed);
      }
      if (out.length > 0) return out.slice(0, lim);
    } catch (err) {
      if (!isEndpointMissingError(err)) throw err;
    }
    try {
      const { data } = await api.get(`/api/forecast/${encodeURIComponent(sym)}`, {
        params: { horizon: h },
        timeout: FORECAST_TIMEOUT_MS
      });
      const r = data ?? {};
      const meta = r.calibration_meta ?? r.calibrationMeta;
      const versions = r.versions ?? {};
      const inputs = r.inputs ?? {};
      const provenance = r.provenance ?? {};
      const single = normalizeEntry({
        brier: r.brier ?? r.brier_score ?? meta?.brier ?? meta?.brier_score ?? null,
        ece: r.ece ?? r.calibration_error ?? meta?.ece ?? meta?.calibration_error ?? null,
        n_windows: r.n_windows ?? inputs.n_windows ?? r.expected_return_range?.n_windows ?? meta?.n_windows ?? null,
        members: r.members ?? null,
        reliability: r.calibration ?? r.reliability ?? [],
        model_version: r.model_version ?? versions.model_version ?? inputs.model_version ?? meta?.model_version ?? null,
        data_version: r.data_version ?? versions.data_version ?? inputs.data_version ?? meta?.data_version ?? null,
        created_at: provenance.as_of ?? meta?.created_at ?? meta?.as_of ?? null
      });
      return single ? [single] : [];
    } catch (err) {
      if (!isEndpointMissingError(err)) throw err;
      return [];
    }
  });
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
export { getCalibrationHistory };
