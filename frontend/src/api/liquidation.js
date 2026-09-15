import { api, coalesceInflight, ProvenanceSchema } from "./client";

// Frontend F2: liquidation-PROXY per market.
//
// Backend: GET /api/markets/{mic}/liquidation-proxy?limit=50&sort=intensity|symbol
//   -> { mic, rows: [{symbol, company_name, price, change_pct, volume,
//        intensity, side, vol_z, range_atr, volume_anomaly}], aggregates,
//        methodology, disclosure, provenance (missing_fields += liquidation-feed) }
// This module only normalizes + fetches. No synthetic rows: empty in -> empty
// out, failures propagate to ErrorState. PROXY labelling is mandatory in UI.

function isRecord(v) {
  return !!v && typeof v === "object" && !Array.isArray(v);
}

function numOrNull(v) {
  if (v === null || v === undefined || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function strOrNull(v) {
  return typeof v === "string" && v.trim() !== "" ? v.trim() : null;
}

function localProvenance(raw, sourceFallback) {
  const r = isRecord(raw) ? raw : {};
  const cand = r.provenance;
  if (isRecord(cand)) {
    const parsed = ProvenanceSchema.passthrough().safeParse(cand);
    if (parsed.success) return parsed.data;
  }
  return {
    source: sourceFallback,
    as_of: new Date().toISOString(),
    delay_minutes: -1,
    quality_grade: "U",
    fallback_used: true,
    missing_fields: ["provenance", "liquidation-feed"],
  };
}

function normalizeLiquidationRow(r) {
  const rec = isRecord(r) ? r : {};
  return {
    symbol: strOrNull(rec.symbol) ?? "UNKNOWN",
    company_name: strOrNull(rec.company_name),
    price: numOrNull(rec.price),
    change_pct: numOrNull(rec.change_pct),
    volume: numOrNull(rec.volume),
    intensity: numOrNull(rec.intensity) ?? 0,
    side: strOrNull(rec.side) ?? "unknown",
    vol_z: numOrNull(rec.vol_z),
    range_atr: numOrNull(rec.range_atr),
    volume_anomaly: rec.volume_anomaly === true,
  };
}

function normalizeLiquidationProxy(raw, micFallback = "") {
  const r = isRecord(raw) ? raw : {};
  const mic = String(r.mic ?? micFallback ?? "").trim().toUpperCase() || "UNKNOWN";
  const rows = Array.isArray(r.rows) ? r.rows.map(normalizeLiquidationRow) : [];
  const agg = isRecord(r.aggregates) ? r.aggregates : {};
  return {
    mic,
    rows,
    aggregates: {
      n: Number.isFinite(Number(agg.n)) ? Number(agg.n) : rows.length,
      skipped: Number.isFinite(Number(agg.skipped)) ? Number(agg.skipped) : 0,
      long_proxy_n: Number.isFinite(Number(agg.long_proxy_n)) ? Number(agg.long_proxy_n) : 0,
      short_proxy_n: Number.isFinite(Number(agg.short_proxy_n)) ? Number(agg.short_proxy_n) : 0,
      mean_intensity: numOrNull(agg.mean_intensity) ?? 0,
      max_intensity: numOrNull(agg.max_intensity) ?? 0,
    },
    methodology: typeof r.methodology === "string" ? r.methodology : "",
    disclosure: typeof r.disclosure === "string" ? r.disclosure : "PROXY — not exchange liquidation data.",
    provenance: localProvenance(r, `liquidation-proxy:${mic}`),
  };
}

function normalizeLiquidationSort(v) {
  const s = String(v ?? "intensity").trim().toLowerCase();
  return s === "symbol" ? "symbol" : "intensity";
}

async function getMarketLiquidationProxy(mic, opts = {}) {
  const upper = String(mic ?? "").trim().toUpperCase() || "UNKNOWN";
  const limit = Math.min(100, Math.max(1, Number(opts?.limit ?? 20) || 20));
  const sort = normalizeLiquidationSort(opts?.sort);
  const signal = opts?.signal;
  return coalesceInflight(`market-liquidation:${upper}:${sort}:${limit}`, async () => {
    const { data } = await api.get(`/api/markets/${encodeURIComponent(upper)}/liquidation-proxy`, {
      params: { limit, sort },
      timeout: 60000,
      ...(signal ? { signal } : {}),
    });
    return normalizeLiquidationProxy(data, upper);
  });
}

function liquidationCacheKey(mic, opts = {}) {
  const upper = String(mic ?? "").trim().toUpperCase() || "UNKNOWN";
  return ["market-liquidation", upper, normalizeLiquidationSort(opts?.sort), Math.min(100, Math.max(1, Number(opts?.limit ?? 20) || 20))];
}

export {
  getMarketLiquidationProxy,
  liquidationCacheKey,
  normalizeLiquidationProxy,
  normalizeLiquidationRow,
  normalizeLiquidationSort,
};
