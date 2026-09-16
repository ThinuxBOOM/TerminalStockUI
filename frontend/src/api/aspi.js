import { api, coalesceInflight, getBars, getScreener } from "./client";
import { getMarketTopRows } from "./markets";

// Frontend 4+5: per-market index ("ASPI-style" benchmark) + Top-20-only
// composite, frontend-only on top of existing quote/bars/screener and
// /api/markets/* endpoints. No new backend routes required; the proposed
// native endpoint shape lives in docs/API_CONTRACT.md (M9 proposal appendix)
// for the backend team.
//
// Data honesty rules (same bar as the rest of the terminal):
// - No hardcoded prices anywhere in this module (config carries SYMBOLS only).
// - Proxy labelling: caret index symbols (^IXIC, ^FCHI, ...) are rejected by
//   backend validate_symbol (SYMBOL_RE has no `^`), so each MIC lists
//   frontend-safe proxy symbols. Charts ALWAYS badge proxy series as PROXY.
// - Native currency only; Top-20 tables never rank across currencies
//   (FX gate respected: no converted_price, no cross-market ranking).

// ---------------------------------------------------------------------------
// Benchmark registry (symbols only — never prices).
//
// Primary = canonical benchmark for the venue. Proxies = frontend-safe
// symbols tried in order when the primary is unreachable (422 on `^`,
// unknown symbol, or empty bars). XSHG needs no proxy: 000001.SS is valid
// under the current backend alphabet.
//
// TODO(backend): relax validate_symbol to allow a leading `^` (or serve the
// proposed GET /api/markets/{mic}/index) so primaries resolve directly.
// TODO(data): confirm Euronext ETF proxy symbols (CAC.PA, IAEX.AS) against
// the vendor feed; EWN/EWQ/EWK (iShares MSCI single-country ETFs) are the
// last-resort proxies and are USD-denominated — the chart labels the actual
// quote currency, never the venue currency, when a proxy is used.
const ASPI_BENCHMARKS = {
  XNYS: {
    mic: "XNYS",
    venue: "NYSE",
    indexLabel: "NYSE Composite",
    indexSymbol: "^NYA",
    proxies: ["SPY"],
    currency: "USD",
    timezone: "America/New_York",
    enabled: true,
  },
  XNAS: {
    mic: "XNAS",
    venue: "Nasdaq",
    indexLabel: "Nasdaq Composite",
    indexSymbol: "^IXIC",
    proxies: ["QQQ"],
    currency: "USD",
    timezone: "America/New_York",
    enabled: true,
  },
  XSHG: {
    mic: "XSHG",
    venue: "SSE",
    indexLabel: "SSE Composite",
    indexSymbol: "000001.SS",
    proxies: [],
    currency: "CNY",
    timezone: "Asia/Shanghai",
    enabled: true,
  },
  XPAR: {
    mic: "XPAR",
    venue: "Euronext Paris",
    indexLabel: "CAC 40",
    indexSymbol: "^FCHI",
    proxies: ["EWQ", "CAC.PA"],
    currency: "EUR",
    timezone: "Europe/Paris",
    enabled: true,
  },
  XAMS: {
    mic: "XAMS",
    venue: "Euronext Amsterdam",
    indexLabel: "AEX",
    indexSymbol: "^AEX",
    proxies: ["EWN", "IAEX.AS"],
    currency: "EUR",
    timezone: "Europe/Amsterdam",
    enabled: true,
  },
  XBRU: {
    mic: "XBRU",
    venue: "Euronext Brussels",
    indexLabel: "BEL 20",
    indexSymbol: "^BFX",
    proxies: ["EWK"],
    currency: "EUR",
    timezone: "Europe/Brussels",
    enabled: true,
  },
  // Extensible slot: Colombo Stock Exchange All-Share Price Index (Sri Lanka
  // ASPI). Disabled until the venue is added to config/markets.yaml (see the
  // M9 proposal appendix); enabling = set enabled:true + confirm the vendor
  // index symbol. Existing 6 MICs are untouched by this entry.
  // TODO(data): confirm the vendor symbol for the CSE ASPI (^CSE unverified).
  XCOL: {
    mic: "XCOL",
    venue: "Colombo (CSE)",
    indexLabel: "CSE All-Share Price Index (ASPI)",
    indexSymbol: "^CSE",
    proxies: [],
    currency: "LKR",
    timezone: "Asia/Colombo",
    enabled: false,
  },
};

const ASPI_MICS = Object.values(ASPI_BENCHMARKS)
  .filter((b) => b.enabled)
  .map((b) => b.mic);
const ALL_ASPI_MICS = Object.keys(ASPI_BENCHMARKS);

// Backend VALID_TIMEFRAMES (backend/security/validation.py); anything else 422s.
const ASPI_TIMEFRAMES = ["1d", "1wk", "1mo"];
const ASPI_LIMIT = { "1d": 90, "1wk": 52, "1mo": 36 };
const TOP20_LIMIT = 20;
const TOP20_BARS_LIMIT = 60;
const TOP20_MIN_SERIES = 3;

// TODO(seed-list): if neither /api/markets/{mic}/liquidity nor /api/screener
// can serve a market, Top-20 could fall back to a curated constituent seed
// list per MIC. Intentionally EMPTY — rendering seed symbols without live
// rows would be fake breadth. Add symbols here only with a plan to enrich
// every row via getQuote; until then the UI shows an honest EmptyState.
const TOP20_SEED = {};

function benchmarkForMic(mic) {
  const upper = String(mic ?? "").trim().toUpperCase();
  return ASPI_BENCHMARKS[upper] ?? null;
}

// ---------------------------------------------------------------------------
// Future-proof cache keys: namespaced by future user_id/tier so per-user or
// tiered index caching can land without key migration. No auth is implemented
// — callers pass nulls today and everything resolves to guest/free.
// e.g. aspi:<mic>:<tf>:<userId||guest>:<tier||free>
function normId(v, fallback) {
  const s = String(v ?? "").trim();
  return s !== "" ? s : fallback;
}

function aspiCacheKey(mic, timeframe = "1d", userId = null, tier = null) {
  const m = String(mic ?? "").trim().toUpperCase() || "UNKNOWN";
  const tf = ASPI_TIMEFRAMES.includes(timeframe) ? timeframe : "1d";
  return ["aspi", m, tf, `u:${normId(userId, "guest")}`, `t:${normId(tier, "free")}`];
}

function aspiInflightKey(mic, timeframe = "1d", userId = null, tier = null) {
  const m = String(mic ?? "").trim().toUpperCase() || "UNKNOWN";
  const tf = ASPI_TIMEFRAMES.includes(timeframe) ? timeframe : "1d";
  return `aspi:${m}:${tf}:${normId(userId, "guest")}:${normId(tier, "free")}`;
}

function top20CacheKey(mic, userId = null, tier = null) {
  const m = String(mic ?? "").trim().toUpperCase() || "UNKNOWN";
  return ["aspi-top20", m, `u:${normId(userId, "guest")}`, `t:${normId(tier, "free")}`];
}

function top20InflightKey(mic, userId = null, tier = null) {
  const m = String(mic ?? "").trim().toUpperCase() || "UNKNOWN";
  return `aspi-top20:${m}:${normId(userId, "guest")}:${normId(tier, "free")}`;
}

// ---------------------------------------------------------------------------
// Pure helpers (deterministic, covered by aspi.test.js).

function numOrNull(v) {
  if (v === null || v === undefined || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function isRecord(v) {
  return !!v && typeof v === "object" && !Array.isArray(v);
}

function strOrNull(v) {
  return typeof v === "string" && v.trim() !== "" ? v : null;
}

// Candles ({time, close}) -> sorted, deduped [{t, close}]. Drops invalid
// rows instead of zero-filling; never invents points.
function closesFromCandles(candles) {
  if (!Array.isArray(candles)) return [];
  const byTime = new Map();
  for (const c of candles) {
    if (!isRecord(c)) continue;
    const t = strOrNull(c.time);
    const close = numOrNull(c.close);
    if (t === null || close === null) continue;
    byTime.set(t, close);
  }
  return [...byTime.entries()]
    .sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0))
    .map(([t, close]) => ({ t, close }));
}

// Normalized per-market index series (what AspiChart renders).
// Fail-closed: throws Error("provenance missing") when provenance is absent —
// never fabricates a fallback envelope. Delays OK, stale/fallback NOT OK.
// closesFromCandles stays pure (drops invalid rows, never invents points).
function normalizeAspiSeries(input) {
  const r = isRecord(input) ? input : {};
  const mic = String(r.mic ?? "").trim().toUpperCase() || "UNKNOWN";
  const points = closesFromCandles(r.candles);
  if (!isRecord(r.provenance)) {
    throw new Error("provenance missing");
  }
  const prov = r.provenance;
  return {
    mic,
    label: strOrNull(r.label) ?? mic,
    symbol: strOrNull(r.symbol) ?? strOrNull(r.usedSymbol) ?? "UNKNOWN",
    usedSymbol: strOrNull(r.usedSymbol) ?? strOrNull(r.symbol) ?? "UNKNOWN",
    isProxy: r.isProxy === true,
    timeframe: strOrNull(r.timeframe) ?? "1d",
    points,
    count: points.length,
    start: points.length > 0 ? points[0].t : null,
    end: points.length > 0 ? points[points.length - 1].t : null,
    lastClose: points.length > 0 ? points[points.length - 1].close : null,
    provenance: prov,
    fallback_used: prov.fallback_used === true,
  };
}

// Rebase a close series to 100 at its first point (unitless index scale).
// Values are rounded to 6dp so rebased points stay exact for base-100
// comparisons and SVG labels (kills FP dust like 110.00000000000001).
function rebaseSeries(points) {
  const clean = (Array.isArray(points) ? points : []).filter(
    (p) => isRecord(p) && strOrNull(p.t) !== null && numOrNull(p.close ?? p.value) !== null
  );
  if (clean.length === 0) return [];
  const base = Number(clean[0].close ?? clean[0].value);
  if (!Number.isFinite(base) || base === 0) return [];
  return clean.map((p) => ({ t: p.t, value: Math.round((Number(p.close ?? p.value) / base) * 100 * 1e6) / 1e6 }));
}

const EQUAL_WEIGHT_METHODOLOGY =
  "Equal-weighted mean of per-constituent rebased closes (base 100 = first " +
  "close in window). Index-weighted needs market-cap weights, which no " +
  "current endpoint exposes — see docs/API_CONTRACT.md M9 proposal.";

const CAP_WEIGHT_METHODOLOGY =
  "Cap-weighted mean of per-constituent rebased closes (base 100), weights = " +
  "per-symbol market-cap when every used constituent carries a finite cap; " +
  "otherwise falls back to equal-weighted with reason=missing market-cap weights.";

// Equal-weighted Top-20 composite over the UNION of dates (a constituent
// contributes only on dates it actually traded — no forward-fill, no
// synthetic points). Requires >= minSeries usable series and >= 2 dates.
// opts.weights: optional {SYMBOL: marketCap} for cap-weighted mode; when
// every used constituent has a finite cap the composite is cap-weighted,
// otherwise it falls back to equal-weighted with an honest reason.
function computeEqualWeightedIndex(seriesList, opts = {}) {
  const minSeries = Number.isFinite(Number(opts.minSeries)) ? Number(opts.minSeries) : TOP20_MIN_SERIES;
  const weights = opts?.weights && typeof opts.weights === "object" ? opts.weights : null;
  const requested = Array.isArray(seriesList) ? seriesList.length : 0;
  const rebased = [];
  for (const s of Array.isArray(seriesList) ? seriesList : []) {
    if (!isRecord(s) || !Array.isArray(s.points) || s.points.length === 0) continue;
    const rb = rebaseSeries(s.points);
    if (rb.length > 0) {
      const sym = strOrNull(s.symbol) ?? "UNKNOWN";
      let cap = null;
      if (weights) {
        const rawCap = weights[sym] ?? weights[String(sym).toUpperCase()];
        const n = Number(rawCap);
        cap = Number.isFinite(n) && n > 0 ? n : null;
      }
      rebased.push({ symbol: sym, points: rb, cap });
    }
  }
  const useCapWeighted = weights !== null && rebased.length > 0 && rebased.every((s) => s.cap !== null);
  const acc = new Map();
  for (const s of rebased) {
    const w = useCapWeighted ? s.cap : 1;
    for (const p of s.points) {
      const hit = acc.get(p.t) ?? { sum: 0, wsum: 0, n: 0 };
      hit.sum += p.value * w;
      hit.wsum += w;
      hit.n += 1;
      acc.set(p.t, hit);
    }
  }
  const dates = [...acc.keys()].sort();
  const weighting = useCapWeighted ? "cap-weighted" : "equal-weighted";
  const base = {
    points: [],
    constituentsUsed: rebased.length,
    constituentsRequested: requested,
    start: dates.length > 0 ? dates[0] : null,
    end: dates.length > 0 ? dates[dates.length - 1] : null,
    methodology: useCapWeighted ? CAP_WEIGHT_METHODOLOGY : EQUAL_WEIGHT_METHODOLOGY,
    weighting,
    reason: weights !== null && !useCapWeighted ? "missing market-cap weights — fell back to equal-weighted" : null,
  };
  if (rebased.length < minSeries) {
    return { ...base, reason: `only ${rebased.length} of ${requested} constituents returned bars (need ${minSeries})` };
  }
  if (dates.length < 2) {
    return { ...base, reason: "fewer than 2 shared trading dates across constituents" };
  }
  return {
    ...base,
    points: dates.map((t) => {
      const hit = acc.get(t);
      const n = hit.n;
      const denom = useCapWeighted ? hit.wsum : n;
      return { t, value: Math.round((hit.sum / denom) * 1e6) / 1e6, n };
    }),
  };
}

function computeCapWeightedIndex(seriesList, weights, opts = {}) {
  return computeEqualWeightedIndex(seriesList, { ...opts, weights });
}

function pickLiquidityRow(r) {
  const rec = isRecord(r) ? r : {};
  return {
    symbol: strOrNull(rec.symbol) ?? "UNKNOWN",
    company_name: strOrNull(rec.company_name),
    currency: strOrNull(rec.currency),
    price: numOrNull(rec.price),
    change_pct: numOrNull(rec.change_pct),
    volume: numOrNull(rec.volume),
    turnover: numOrNull(rec.turnover),
    range_pct: numOrNull(rec.range_pct),
    market_state: strOrNull(rec.market_state),
    market_cap: numOrNull(rec.market_cap ?? rec.marketCap ?? rec.cap),
  };
}

function pickScreenerRow(r) {
  const rec = isRecord(r) ? r : {};
  return {
    symbol: strOrNull(rec.symbol) ?? "UNKNOWN",
    company_name: strOrNull(rec.company_name),
    currency: strOrNull(rec.currency),
    price: numOrNull(rec.price),
    change_pct: numOrNull(rec.change_pct),
    volume: numOrNull(rec.volume),
    turnover: numOrNull(rec.turnover),
    range_pct: numOrNull(rec.range_pct),
    market_state: strOrNull(rec.market_state),
    market_cap: numOrNull(rec.market_cap ?? rec.marketCap ?? rec.cap),
  };
}

// Fill company_name/currency/market_cap gaps in liquidity rows from screener
// results (matched by symbol). Never overwrites a present value, never
// invents one. market_cap is the only cap-weighted opt-in (turnover ignored).
function enrichConstituentsWithScreener(rows, screenerResults) {
  const bySymbol = new Map();
  for (const s of Array.isArray(screenerResults) ? screenerResults : []) {
    if (!isRecord(s)) continue;
    const sym = strOrNull(s.symbol);
    if (sym !== null && !bySymbol.has(sym.toUpperCase())) bySymbol.set(sym.toUpperCase(), s);
  }
  return (Array.isArray(rows) ? rows : []).map((row) => {
    const rec = isRecord(row) ? { ...row } : { ...pickLiquidityRow(row) };
    const hit = bySymbol.get(String(rec.symbol ?? "").toUpperCase());
    if (hit) {
      if (strOrNull(rec.company_name) === null && strOrNull(hit.company_name) !== null) {
        rec.company_name = hit.company_name;
      }
      if (strOrNull(rec.currency) === null && strOrNull(hit.currency) !== null) {
        rec.currency = String(hit.currency).trim().toUpperCase();
      }
      if (numOrNull(rec.market_cap) === null) {
        const cap = numOrNull(hit.market_cap ?? hit.marketCap ?? hit.cap);
        if (cap !== null && cap > 0) rec.market_cap = cap;
      }
    }
    return rec;
  });
}

// Top-20 selection. Preferred: liquidity rows (server-sorted by native
// turnover). Fallback: screener rank order (direction_probability) —
// labelled as fallback. Empty in -> empty out (seed list stays a TODO;
// fake constituents are never rendered).
// DEAD live path — getTop20Constituents never passes screenerRows (fail-closed).
// The screener-rank-fallback branch below is kept as a pure, honestly-labelled
// helper covered by unit tests; it is never reached by live callers.
function normalizeTop20(input = {}, micFallback = "") {
  const r = isRecord(input) ? input : {};
  const mic = String(r.mic ?? micFallback ?? "").trim().toUpperCase() || "UNKNOWN";
  const limit = Math.min(50, Math.max(1, Number(r.limit ?? TOP20_LIMIT) || TOP20_LIMIT));
  const liqRows = Array.isArray(r.liquidityRows) ? r.liquidityRows : null;
  const scrRows = Array.isArray(r.screenerRows) ? r.screenerRows : null;
  if (liqRows !== null && liqRows.length > 0) {
    return {
      mic,
      rows: liqRows.slice(0, limit).map(pickLiquidityRow),
      methodology: "liquidity-turnover",
      methodologyNote:
        `Top ${Math.min(limit, liqRows.length)} by native turnover (price x volume, no FX) ` +
        `from GET /api/markets/${mic}/liquidity?sort=turnover&limit=${limit}.`,
      reason: null,
    };
  }
  if (scrRows !== null && scrRows.length > 0) {
    return {
      mic,
      rows: scrRows.slice(0, limit).map(pickScreenerRow),
      methodology: "screener-rank-fallback",
      methodologyNote:
        `Liquidity endpoint unavailable — first ${Math.min(limit, scrRows.length)} screener rows ` +
        `(ranked by forecast direction_probability, NOT by size). Turnover-sorted ` +
        `Top-20 needs GET /api/markets/${mic}/liquidity (see API proposal).`,
      reason: null,
    };
  }
  return {
    mic,
    rows: [],
    methodology: "unavailable",
    methodologyNote: "",
    reason:
      "No liquidity or screener rows for this market (TOP20_SEED is an empty " +
      "TODO by design — constituents are never fabricated).",
  };
}

// Merge per-series provenance envelopes into one composite envelope:
// oldest as_of wins, sources joined, fallback sticky, missing unioned,
// delay = max, grade = worst (fallback forces D).
// Empty input: honest non-fallback envelope (nothing served, nothing fallback)
// to mirror backend empty-scan contract — fallback_used FALSE, delay 15.
function combineAspiProvenance(entries, sourceFallback = "aspi:composite") {
  const list = (Array.isArray(entries) ? entries : []).filter(isRecord);
  if (list.length === 0) {
    return {
      source: sourceFallback,
      as_of: new Date().toISOString(),
      delay_minutes: 15,
      quality_grade: "U",
      fallback_used: false,
      missing_fields: ["provenance"],
    };
  }
  let oldest = null;
  for (const e of list) {
    const ms = Date.parse(e.as_of);
    if (Number.isFinite(ms) && (oldest === null || ms < oldest)) oldest = ms;
  }
  const sources = [...new Set(list.map((e) => String(e.source ?? "unknown")))].sort();
  const fallback = list.some((e) => e.fallback_used === true);
  const missing = [...new Set(list.flatMap((e) => (Array.isArray(e.missing_fields) ? e.missing_fields : []).map(String)))].sort();
  const delays = list.map((e) => Number(e.delay_minutes)).filter((n) => Number.isFinite(n));
  const rank = { A: 0, B: 1, C: 2, D: 3, U: 4 };
  let grade = "U";
  let worst = -1;
  for (const e of list) {
    const g = String(e.quality_grade ?? "U").trim().toUpperCase();
    const r = rank[g] ?? 4;
    if (r > worst) {
      worst = r;
      grade = rank[g] !== undefined ? g : "U";
    }
  }
  return {
    source: sources.join("+") || sourceFallback,
    as_of: oldest !== null ? new Date(oldest).toISOString() : new Date().toISOString(),
    delay_minutes: delays.length > 0 ? Math.max(...delays) : -1,
    quality_grade: fallback ? "D" : grade,
    fallback_used: fallback,
    missing_fields: missing,
  };
}

// ---------------------------------------------------------------------------
// Fetchers (frontend-only; existing endpoints only).

function httpStatus(err) {
  const e = err;
  const s = e?.response?.status ?? e?.status;
  return typeof s === "number" ? s : null;
}

function isEndpointMissingError(err) {
  const s = httpStatus(err);
  return s === 404 || s === 501;
}

// Per-market benchmark series. Prefers the native backend endpoint
// GET /api/markets/{mic}/index (server-side proxy chain, same symbols as
// below); falls back to the frontend getBars proxy chain when the endpoint
// is missing (404/501), on network errors, or on 502 (cold DB / Yahoo
// throttle — transient, retryable via the per-proxy bars chain which rides
// the 120s bars cache). Fail-closed only on 422 (disabled venue / bad
// symbol): that is a fatal answer, never a second opinion. Zero-candle
// successes count as a miss.
function normalizeNativeIndexSeries(raw, micFallback = "") {
  const r = isRecord(raw) ? raw : {};
  const mic = String(r.mic ?? micFallback ?? "").trim().toUpperCase() || "UNKNOWN";
  const listRaw = Array.isArray(r.points) ? r.points : [];
  const candles = [];
  for (const p of listRaw) {
    if (!isRecord(p)) continue;
    const t = strOrNull(p.t);
    const close = numOrNull(p.close ?? p.value);
    if (t === null || close === null) continue;
    // Native points carry close only; synthesize flat OHLC so the shared
    // closesFromCandles path stays exact (no invented range).
    candles.push({ time: t, open: close, high: close, low: close, close });
  }
  return normalizeAspiSeries({
    mic,
    label: strOrNull(r.label) ?? mic,
    symbol: strOrNull(r.symbol) ?? strOrNull(r.usedSymbol) ?? "UNKNOWN",
    usedSymbol: strOrNull(r.usedSymbol) ?? strOrNull(r.symbol) ?? "UNKNOWN",
    isProxy: r.is_proxy === true || r.isProxy === true,
    timeframe: strOrNull(r.timeframe) ?? "1d",
    candles,
    provenance: isRecord(r.provenance) ? r.provenance : undefined,
  });
}

async function getAspiSeries(mic, timeframe = "1d", opts = {}) {
  const cfg = benchmarkForMic(mic);
  if (!cfg || cfg.enabled !== true) {
    throw new Error(`no benchmark configured for market ${String(mic ?? "").trim().toUpperCase() || "UNKNOWN"}`);
  }
  const tf = ASPI_TIMEFRAMES.includes(timeframe) ? timeframe : "1d";
  const limit = ASPI_LIMIT[tf] ?? 90;
  const userId = opts?.userId ?? null;
  const tier = opts?.tier ?? null;
  const signal = opts?.signal;
  return coalesceInflight(aspiInflightKey(cfg.mic, tf, userId, tier), async () => {
    // Native endpoint first (backend owns the proxy chain + provenance).
    // 20s fail-fast: a hung index fetch must fall through to the bars chain
    // instead of holding the chart in a 60s spinner.
    try {
      const { data } = await api.get(`/api/markets/${encodeURIComponent(cfg.mic)}/index`, {
        params: { timeframe: tf },
        timeout: 20000,
        ...(signal ? { signal } : {}),
      });
      const native = normalizeNativeIndexSeries(data, cfg.mic);
      if (native.points.length > 0) return native;
    } catch (err) {
      // Fail-closed on 422 (disabled venue / bad symbol): fatal, throw
      // immediately. 502 (cold DB / throttle) is transient — fall through
      // to the frontend bars chain below, which tries each proxy via the
      // cached getBars path. Only 422 throws here.
      if (httpStatus(err) === 422) {
        throw err;
      }
      // 502/504/404/501/network/timeout: frontend chain below.
      try {
        return await getAspiSeriesViaBars(cfg, tf, limit, signal);
      } catch (chainErr) {
        // Preserve both errors: the chain tried every proxy, but the native
        // failure (throttle vs missing endpoint) decides the user copy.
        // Timeout gets a friendly message; otherwise the chain detail wins.
        const chainMsg =
          chainErr instanceof Error && chainErr.message
            ? chainErr.message
            : String(chainErr ?? "index bars chain failed");
        const nativeTimeout =
          err?.code === "ECONNABORTED" ||
          /timeout of \d+ms exceeded/i.test(String(err?.message ?? ""));
        const msg = nativeTimeout
          ? `${chainMsg} (native index timed out — cold start, retry; warm cache is fast)`
          : chainMsg;
        throw new Error(msg, { cause: { native: err, chain: chainErr } });
      }
    }
    return getAspiSeriesViaBars(cfg, tf, limit, signal);
  });
}

async function getAspiSeriesViaBars(cfg, tf, limit, signal) {
  const candidates = [
    { symbol: cfg.indexSymbol, isProxy: false },
    ...(Array.isArray(cfg.proxies) ? cfg.proxies : []).map((p) => ({ symbol: p, isProxy: true })),
  ];
  // Sequential in preference order (primary first), but each candidate gets
  // its own 15s fail-fast and the whole chain gets a 30s overall deadline so
  // 1 primary + 2 proxies can never hang 180s. All per-candidate errors are
  // preserved for the AggregateError when everything misses.
  const errors = [];
  let sawEmpty = false;
  const deadlineMs = 30000;
  const startedAt = Date.now();
  for (const c of candidates) {
    const elapsed = Date.now() - startedAt;
    if (elapsed >= deadlineMs) {
      errors.push(new Error(`chain budget exceeded after ${elapsed}ms before trying ${c.symbol}`));
      break;
    }
    const remaining = Math.max(3000, Math.min(15000, deadlineMs - elapsed));
    try {
      const bars = await getBars(c.symbol, tf, limit, {
        ...(signal ? { signal } : {}),
        timeout: remaining,
      });
      const candles = Array.isArray(bars?.candles) ? bars.candles : [];
      if (candles.length === 0) {
        sawEmpty = true;
        errors.push(new Error(`empty bars for ${c.symbol}`));
        continue;
      }
      return normalizeAspiSeries({
        mic: cfg.mic,
        label: `${cfg.venue} — ${cfg.indexLabel}`,
        symbol: bars.symbol ?? c.symbol,
        usedSymbol: c.symbol,
        isProxy: c.isProxy,
        timeframe: bars.timeframe ?? tf,
        candles,
        provenance: bars.provenance,
      });
    } catch (err) {
      errors.push(err instanceof Error ? err : new Error(String(err ?? `bars failed for ${c.symbol}`)));
      // Aborted outer signal: stop trying further proxies immediately.
      if (signal?.aborted) break;
    }
  }
  if (errors.length > 0) {
    const first = errors[0];
    const msg =
      first instanceof Error && first.message ? first.message : `index unavailable for ${cfg.mic}`;
    const agg =
      typeof AggregateError !== "undefined"
        ? new AggregateError(errors, msg)
        : Object.assign(new Error(msg), { errors });
    // Empty-but-no-throw case stays honest about proxies tried.
    if (errors.every((e) => String(e?.message ?? "").startsWith("empty bars")) || sawEmpty) {
      throw new Error(
        `index bars empty for ${cfg.mic} (${cfg.indexSymbol}) — no proxy configured`,
        { cause: { errors } }
      );
    }
    throw agg;
  }
  throw new Error(
    sawEmpty
      ? `index bars empty for ${cfg.mic} (${cfg.indexSymbol}) — no proxy configured`
      : `index unavailable for ${cfg.mic}`
  );
}

// Top-20 constituents for a market: server-side turnover-sorted liquidity
// rows (limit 20) enriched with company/currency/cap from the screener.
// Fail-closed: liquidity errors propagate to ErrorState — no
// screener-rank synthesis. (normalizeTop20 keeps its screener-rank branch
// as a pure, honestly-labelled helper covered by unit tests.)
// Perf: screener enrichment is best-effort with an 8s race — a full screener
// scan (quote+forecast per symbol) must never hold the Top-20 table hostage
// behind a 60s timeout.
async function getTop20Constituents(mic, opts = {}) {
  const upper = String(mic ?? "").trim().toUpperCase() || "UNKNOWN";
  const userId = opts?.userId ?? null;
  const tier = opts?.tier ?? null;
  const signal = opts?.signal;
  return coalesceInflight(top20InflightKey(upper, userId, tier), async () => {
    // Liquidity is preferred; on 502/throw fall back to honestly-labelled
    // screener-rank (never a dead ErrorState when a second opinion exists).
    let liq = null;
    let liqError = null;
    try {
      liq = await getMarketTopRows(upper, { limit: TOP20_LIMIT, sort: "turnover" });
    } catch (err) {
      liqError = err;
      liq = null;
    }
    const liqRows = Array.isArray(liq?.rows) ? liq.rows : [];
    // Screener enrichment: best-effort 8s race with proper cleanup — the
    // timer is cleared on settle and the inner fetch is aborted on timeout
    // or outer-signal abort so nothing runs 60s in the background.
    let screenerResults = [];
    {
      const inner = new AbortController();
      const onOuterAbort = () => {
        try {
          inner.abort();
        } catch {
          // never throws
        }
      };
      if (signal) {
        if (signal.aborted) inner.abort();
        else signal.addEventListener("abort", onOuterAbort, { once: true });
      }
      let timer = null;
      try {
        const screenP = getScreener(
          { market: upper, minDirection: 0, limit: TOP20_LIMIT },
          { signal: inner.signal }
        );
        const timeoutP = new Promise((resolve) => {
          timer = setTimeout(() => {
            try {
              inner.abort();
            } catch {
              // never throws
            }
            resolve(null);
          }, 8000);
        });
        const screen = await Promise.race([screenP, timeoutP]);
        screenerResults = Array.isArray(screen?.results) ? screen.results : [];
      } catch {
        screenerResults = [];
      } finally {
        if (timer !== null) clearTimeout(timer);
        if (signal) {
          try {
            signal.removeEventListener("abort", onOuterAbort);
          } catch {
            // never throws
          }
        }
      }
    }
    // Liquidity hit: normal path (honest provenance, no fallback flag).
    if (liq && liqRows.length > 0) {
      const picked = normalizeTop20({ mic: upper, liquidityRows: liqRows, limit: TOP20_LIMIT }, upper);
      const prov = isRecord(liq?.provenance)
        ? liq.provenance
        : {
            source: `aspi-top20:${upper}`,
            as_of: new Date().toISOString(),
            delay_minutes: -1,
            quality_grade: "D",
            fallback_used: true,
            missing_fields: ["provenance"],
          };
      return {
        ...picked,
        rows: enrichConstituentsWithScreener(picked.rows, screenerResults),
        provenance: prov,
        fallback_used: Boolean(prov?.fallback_used),
      };
    }
    // Liquidity miss but screener available: honestly-labelled fallback
    // (methodology=screener-rank-fallback, fallback_used=true). Both miss:
    // rethrow the liquidity error so the UI ErrorState shows the root cause.
    if (screenerResults.length > 0) {
      const picked = normalizeTop20(
        { mic: upper, liquidityRows: [], screenerRows: screenerResults, limit: TOP20_LIMIT },
        upper
      );
      return {
        ...picked,
        rows: enrichConstituentsWithScreener(picked.rows, screenerResults),
        provenance: {
          source: `aspi-top20:${upper}:screener-fallback`,
          as_of: new Date().toISOString(),
          delay_minutes: -1,
          quality_grade: "D",
          fallback_used: true,
          missing_fields: ["liquidity", ...(liqError ? ["liquidity-error"] : [])],
        },
        fallback_used: true,
      };
    }
    if (liqError) throw liqError;
    const picked = normalizeTop20({ mic: upper, liquidityRows: [], limit: TOP20_LIMIT }, upper);
    return {
      ...picked,
      rows: [],
      provenance: {
        source: `aspi-top20:${upper}`,
        as_of: new Date().toISOString(),
        delay_minutes: -1,
        quality_grade: "D",
        fallback_used: true,
        missing_fields: ["liquidity", "screener"],
      },
      fallback_used: true,
    };
  });
}

// Bars for each Top-20 constituent (for the equal-weighted composite).
// allSettled: one bad symbol never kills the composite; skips are reported.
// Concurrency 6 + 30s overall budget: 20 parallel 60s getBars used to hold
// the composite past the Vercel maxDuration and spam Yahoo from cloud IPs.
async function getTop20Bars(symbols, timeframe = "1d", opts = {}) {
  const tf = ASPI_TIMEFRAMES.includes(timeframe) ? timeframe : "1d";
  const limit = Math.min(ASPI_LIMIT[tf] ?? 90, TOP20_BARS_LIMIT);
  const signal = opts?.signal;
  const clean = [...new Set((Array.isArray(symbols) ? symbols : []).map((s) => String(s ?? "").trim()).filter(Boolean))].slice(
    0,
    TOP20_LIMIT
  );
  const runOne = (sym) => getBars(sym, tf, limit, { ...(signal ? { signal } : {}), timeout: 15000 });
  const settled = await Promise.race([
    (async () => {
      // Simple pool of 6.
      const out = new Array(clean.length);
      let idx = 0;
      async function worker() {
        while (idx < clean.length) {
          const i = idx++;
          try {
            out[i] = { status: "fulfilled", value: await runOne(clean[i]) };
          } catch (e) {
            out[i] = { status: "rejected", reason: e };
          }
        }
      }
      await Promise.all(Array.from({ length: Math.min(6, clean.length) }, worker));
      return out;
    })(),
    new Promise((resolve) => setTimeout(() => resolve(null), 30000)),
  ]);
  const finalSettled = Array.isArray(settled)
    ? settled
    : clean.map((sym) => ({ status: "rejected", reason: new Error("top20 bars timed out after 30s") }));
  const series = [];
  const skipped = [];
  finalSettled.forEach((s, i) => {
    const sym = clean[i];
    if (s.status !== "fulfilled") {
      skipped.push({ symbol: sym, reason: s.reason instanceof Error ? s.reason.message : String(s.reason ?? "bars failed") });
      return;
    }
    const points = closesFromCandles(s.value?.candles);
    if (points.length === 0) {
      skipped.push({ symbol: sym, reason: "no bars returned" });
      return;
    }
    series.push({ symbol: s.value?.symbol ?? sym, points, provenance: s.value?.provenance ?? null });
  });
  return { series, skipped };
}

export {
  ALL_ASPI_MICS,
  ASPI_BENCHMARKS,
  ASPI_LIMIT,
  ASPI_MICS,
  ASPI_TIMEFRAMES,
  CAP_WEIGHT_METHODOLOGY,
  EQUAL_WEIGHT_METHODOLOGY,
  TOP20_LIMIT,
  TOP20_MIN_SERIES,
  TOP20_SEED,
  aspiCacheKey,
  aspiInflightKey,
  benchmarkForMic,
  closesFromCandles,
  combineAspiProvenance,
  computeCapWeightedIndex,
  computeEqualWeightedIndex,
  enrichConstituentsWithScreener,
  getAspiSeries,
  getTop20Bars,
  getTop20Constituents,
  normalizeAspiSeries,
  normalizeNativeIndexSeries,
  normalizeTop20,
  rebaseSeries,
  top20CacheKey,
  top20InflightKey,
};
