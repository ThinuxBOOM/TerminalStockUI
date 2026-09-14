import { z } from "zod";
import { api, coalesceInflight, getQuote, getScreener, normalizeSymbolParam, ProvenanceSchema, SUPPORTED_MARKET_MICS } from "./client";
const MARKET_MICS = [...SUPPORTED_MARKET_MICS];
const MARKET_LABELS = {
  XNYS: "NYSE (XNYS)",
  XNAS: "NASDAQ (XNAS)",
  XSHG: "SSE (XSHG)",
  XPAR: "Euronext Paris (XPAR)",
  XAMS: "Euronext Amsterdam (XAMS)",
  XBRU: "Euronext Brussels (XBRU)"
};
const MarketBreadthSchema = z.object({
  mic: z.string(),
  label: z.string().optional().default(""),
  advancers: z.number().optional().default(0),
  decliners: z.number().optional().default(0),
  unchanged: z.number().optional().default(0),
  total: z.number().optional().default(0),
  avg_change_pct: z.number().nullable().optional(),
  total_volume: z.number().nullable().optional(),
  turnover: z.number().nullable().optional(),
  avg_range_pct: z.number().nullable().optional(),
  market_state_counts: z.record(z.number()).optional().default({}),
  provenance: ProvenanceSchema
}).passthrough();
function isStaleLiquidity(p) {
  if (!p || typeof p !== "object") return true;
  if (p.fallback_used) return true;
  return String(p.quality_grade ?? "").trim().toUpperCase() === "D";
}
function isRecord(v) {
  return !!v && typeof v === "object" && !Array.isArray(v);
}
function numOrNull(v) {
  if (v === null || v === void 0 || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}
function numOrZero(v) {
  const n = Number(v);
  return Number.isFinite(n) && n >= 0 ? Math.floor(n) : 0;
}
function str(v, fallback = "") {
  return typeof v === "string" && v.trim() !== "" ? v : fallback;
}
function localProvenance(raw, sourceFallback) {
  const r = isRecord(raw) ? raw : {};
  const nested = isRecord(r.meta) ? r.meta.provenance : void 0;
  const cand = r.provenance ?? nested;
  if (isRecord(cand)) {
    const parsed = ProvenanceSchema.passthrough().safeParse(cand);
    if (parsed.success) return parsed.data;
  }
  const delayRaw = r.delay_minutes ?? (isRecord(cand) ? cand.delay_minutes : void 0);
  return {
    source: str(r.source, sourceFallback),
    as_of: str(r.as_of, (/* @__PURE__ */ new Date()).toISOString()),
    delay_minutes: typeof delayRaw === "number" ? Number(delayRaw) : -1,
    quality_grade: str(
      r.quality_grade ?? r.data_quality ?? r.grade,
      "U"
    ),
    fallback_used: true,
    missing_fields: ["provenance"]
  };
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
function normalizeMarketBreadth(raw, micFallback = "") {
  const r = isRecord(raw) ? raw : {};
  const mic = str(
    r.mic ?? r.exchange_mic ?? r.market,
    micFallback || "UNKNOWN"
  ).toUpperCase();
  const advancers = numOrZero(r.advancers ?? r.adv ?? r.up ?? r.advancing);
  const decliners = numOrZero(r.decliners ?? r.dec ?? r.down ?? r.declining);
  const unchanged = numOrZero(r.unchanged ?? r.unch ?? r.flat ?? r.unchanged_count);
  const totalRaw = numOrZero(
    r.total ?? r.count ?? r.universe ?? r.universe_size ?? r.symbols_total ?? r.quoted
  );
  const total = totalRaw > 0 ? totalRaw : advancers + decliners + unchanged;
  const countsRaw = isRecord(r.market_state_counts) ? r.market_state_counts : isRecord(r.state_counts) ? r.state_counts : isRecord(r.by_state) ? r.by_state : {};
  const market_state_counts = {};
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
      r.avg_change_pct ?? r.avgChangePct ?? r.avg_change ?? r.mean_change_pct
    ),
    total_volume: numOrNull(
      r.total_volume ?? r.volume ?? r.totalVolume ?? r.volume_shares
    ),
    turnover: numOrNull(
      r.turnover ?? r.notional ?? r.turnover_value ?? r.total_turnover
    ),
    avg_range_pct: numOrNull(
      r.avg_range_pct ?? r.avgRangePct ?? r.avg_range ?? r.mean_range_pct
    ),
    market_state_counts,
    rows: normalizeMarketRows(r.rows ?? r.symbols ?? r.details ?? r.items),
    turnover_note: typeof r.turnover_note === "string" && r.turnover_note.trim() !== "" ? r.turnover_note : null,
    provenance: localProvenance(r, `markets-api:${mic}`)
  };
}
function normalizeMarketRows(raw) {
  if (!Array.isArray(raw)) return [];
  const out = [];
  for (const item of raw) {
    if (!isRecord(item)) continue;
    const symbol = str(item.symbol ?? item.exchange_symbol ?? item.provider_symbol);
    if (!symbol) continue;
    const st = item.market_state ?? item.state ?? item.marketState;
    out.push({
      symbol,
      price: numOrNull(item.price ?? item.last ?? item.close),
      // NOTE: absolute `change` (currency units) must never stand in for
      // percent change — a $1.75 move would render as +1.75%.
      change_pct: numOrNull(item.change_pct ?? item.changePct),
      volume: numOrNull(item.volume ?? item.volume_shares ?? item.total_volume),
      turnover: numOrNull(item.turnover ?? item.notional ?? item.total_turnover),
      range_pct: numOrNull(item.range_pct ?? item.rangePct ?? item.day_range_pct),
      market_state: typeof st === "string" && st ? st : null
    });
  }
  return out;
}
function normalizeMarketsOverview(raw) {
  const r = isRecord(raw) ? raw : {};
  const listRaw = Array.isArray(raw) ? raw : Array.isArray(r.markets) ? r.markets : Array.isArray(r.data) ? r.data : Array.isArray(r.results) ? r.results : Array.isArray(r.items) ? r.items : [];
  const topNote = typeof r.turnover_note === "string" && r.turnover_note.trim() !== "" ? r.turnover_note : null;
  const markets = listRaw.map((row, i) => {
    const parsed = normalizeMarketBreadth(row, MARKET_MICS[i] ?? "");
    if (!parsed.turnover_note && topNote) parsed.turnover_note = topNote;
    return parsed;
  });
  const provenance = localProvenance(raw, "markets-api");
  const explicitFallback = typeof r.fallback_used === "boolean" ? r.fallback_used : false;
  return {
    markets,
    provenance,
    fallback_used: explicitFallback || provenance.fallback_used || markets.some((m) => m.provenance.fallback_used)
  };
}
function rowVolume(row) {
  const r = row;
  return numOrNull(r.volume ?? r.total_volume ?? r.volume_shares ?? r.shares_volume);
}
function rowRangePct(row) {
  const r = row;
  return numOrNull(r.range_pct ?? r.rangePct ?? r.day_range_pct ?? r.avg_range_pct);
}
function computeBreadthFromScreener(mic, rows) {
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
  const market_state_counts = {};
  const rowMissing = /* @__PURE__ */ new Set();
  for (const row of rows) {
    const cp = numOrNull(row.change_pct);
    if (cp === null) {
    } else if (cp > 0) advancers += 1;
    else if (cp < 0) decliners += 1;
    else unchanged += 1;
    if (cp !== null) {
      changeSum += cp;
      changeN += 1;
    }
    const st = typeof row.market_state === "string" && row.market_state ? row.market_state : "unknown";
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
  const missing = /* @__PURE__ */ new Set(["markets-overview-endpoint"]);
  if (volN === 0) missing.add("volume");
  if (turnoverN === 0) missing.add("turnover");
  if (rangeN === 0) missing.add("avg_range_pct");
  if (rows.length > 0 && changeN === 0) missing.add("change_pct");
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
    turnover_note: null,
    // client-side fallback: no native-currency disclaimer source
    rows: rows.map((row) => ({
      symbol: row.symbol,
      price: numOrNull(row.price),
      change_pct: numOrNull(row.change_pct),
      volume: rowVolume(row),
      turnover: rowVolume(row) !== null && numOrNull(row.price) !== null ? rowVolume(row) * numOrNull(row.price) : null,
      range_pct: rowRangePct(row),
      market_state: typeof row.market_state === "string" && row.market_state ? row.market_state : null
    })),
    provenance: {
      source: `client-fallback:screener${rows.length === 0 ? ":empty" : ""}`,
      as_of: (/* @__PURE__ */ new Date()).toISOString(),
      delay_minutes: -1,
      quality_grade: "D",
      fallback_used: true,
      missing_fields: [...missing]
    }
  };
}
const QUOTE_ENRICH_CAP = 12;
async function enrichWithQuotes(rows) {
  const targets = rows.filter((r) => numOrNull(r.change_pct) === null).slice(0, QUOTE_ENRICH_CAP);
  if (targets.length === 0) return;
  const settled = await Promise.allSettled(targets.map((r) => getQuote(r.symbol)));
  const bySymbol = /* @__PURE__ */ new Map();
  settled.forEach((s, i) => {
    if (s.status !== "fulfilled") return;
    bySymbol.set(normalizeSymbolParam(targets[i].symbol), {
      change_pct: s.value.change_pct,
      market_state: s.value.market_state
    });
  });
  for (const row of rows) {
    if (numOrNull(row.change_pct) !== null) continue;
    const hit = bySymbol.get(normalizeSymbolParam(row.symbol));
    if (!hit) continue;
    const rec = row;
    if (numOrNull(hit.change_pct) !== null) rec.change_pct = Number(hit.change_pct);
    if (!row.market_state && typeof hit.market_state === "string") {
      rec.market_state = hit.market_state;
    }
  }
}
async function getMarketLiquidityFallback(mic) {
  const upper = String(mic ?? "").trim().toUpperCase();
  const screen = await getScreener({ market: upper, minDirection: 0, limit: 50 });
  const rows = [...screen.results];
  await enrichWithQuotes(rows);
  return computeBreadthFromScreener(upper, rows);
}
async function getMarketsOverviewFallback() {
  const settled = await Promise.allSettled(
    MARKET_MICS.map((mic) => getMarketLiquidityFallback(mic))
  );
  const markets = [];
  let firstError = new Error("screener fallback failed for every market");
  let sawError = false;
  for (const s of settled) {
    if (s.status === "fulfilled") markets.push(s.value);
    else if (!sawError) {
      sawError = true;
      firstError = s.reason;
    }
  }
  if (markets.length === 0) throw firstError;
  const missing = /* @__PURE__ */ new Set(["markets-overview-endpoint"]);
  for (const m of markets) for (const f of m.provenance.missing_fields) missing.add(f);
  return {
    markets,
    provenance: {
      source: "client-fallback:screener+quote",
      as_of: (/* @__PURE__ */ new Date()).toISOString(),
      delay_minutes: -1,
      quality_grade: "D",
      fallback_used: true,
      missing_fields: [...missing]
    },
    fallback_used: true
  };
}
async function getMarketsOverview() {
  return coalesceInflight("markets-overview", async () => {
    try {
      const { data } = await api.get("/api/markets/overview", { timeout: 6e4 });
      return normalizeMarketsOverview(data);
    } catch (err) {
      if (!isEndpointMissingError(err)) throw err;
      return getMarketsOverviewFallback();
    }
  });
}
async function getMarketLiquidity(mic) {
  const upper = String(mic ?? "").trim().toUpperCase();
  return coalesceInflight(`market-liquidity:${upper}`, async () => {
    try {
      const { data } = await api.get(`/api/markets/${encodeURIComponent(upper)}/liquidity`, {
        timeout: 6e4
      });
      return normalizeMarketBreadth(data, upper);
    } catch (err) {
      if (!isEndpointMissingError(err)) throw err;
      return getMarketLiquidityFallback(upper);
    }
  });
}
export { MARKET_LABELS, MARKET_MICS, MarketBreadthSchema, computeBreadthFromScreener, getMarketLiquidity, getMarketsOverview, isStaleLiquidity, normalizeMarketBreadth, normalizeMarketRows, normalizeMarketsOverview };
