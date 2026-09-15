import { describe, expect, it } from "vitest";
import {
  ALL_ASPI_MICS,
  ASPI_BENCHMARKS,
  ASPI_MICS,
  ASPI_TIMEFRAMES,
  TOP20_LIMIT,
  TOP20_MIN_SERIES,
  TOP20_SEED,
  aspiCacheKey,
  aspiInflightKey,
  benchmarkForMic,
  closesFromCandles,
  combineAspiProvenance,
  computeEqualWeightedIndex,
  enrichConstituentsWithScreener,
  normalizeAspiSeries,
  normalizeTop20,
  rebaseSeries,
  top20CacheKey,
  top20InflightKey,
} from "./aspi";

const TS = "2026-01-15T12:00:00.000Z";
const TS_OLD = "2026-01-10T12:00:00.000Z";

function prov(over = {}) {
  return {
    source: "bars-api",
    as_of: TS,
    delay_minutes: 15,
    quality_grade: "B",
    fallback_used: false,
    missing_fields: [],
    ...over,
  };
}

describe("ASPI_BENCHMARKS registry (symbols only — never prices)", () => {
  it("covers the 6 live MICs, all enabled", () => {
    expect([...ASPI_MICS].sort()).toEqual(["XAMS", "XBRU", "XNAS", "XNYS", "XPAR", "XSHG"]);
    for (const mic of ASPI_MICS) {
      expect(ASPI_BENCHMARKS[mic].enabled).toBe(true);
    }
  });
  it("keeps the XCOL (Sri Lanka ASPI) slot extensible but disabled", () => {
    expect(ALL_ASPI_MICS).toContain("XCOL");
    expect(ASPI_MICS).not.toContain("XCOL");
    expect(ASPI_BENCHMARKS.XCOL.enabled).toBe(false);
    expect(ASPI_BENCHMARKS.XCOL.currency).toBe("LKR");
  });
  it("carries symbols/currency/timezone and zero price fields", () => {
    for (const [mic, cfg] of Object.entries(ASPI_BENCHMARKS)) {
      expect(cfg.mic).toBe(mic);
      expect(typeof cfg.indexSymbol).toBe("string");
      expect(cfg.indexSymbol.length).toBeGreaterThan(0);
      expect(Array.isArray(cfg.proxies)).toBe(true);
      expect(typeof cfg.currency).toBe("string");
      expect(typeof cfg.timezone).toBe("string");
      // No numeric market data may live in config — every number is fetched.
      for (const [k, v] of Object.entries(cfg)) {
        expect(typeof v === "number" ? `${k} must not be numeric` : "ok").toBe("ok");
      }
    }
  });
  it("XSHG needs no proxy (000001.SS passes backend validation)", () => {
    expect(ASPI_BENCHMARKS.XSHG.indexSymbol).toBe("000001.SS");
    expect(ASPI_BENCHMARKS.XSHG.proxies).toEqual([]);
  });
});

describe("TOP20_SEED (never fabricated)", () => {
  it("is empty by design — constituents are never invented", () => {
    expect(TOP20_SEED).toEqual({});
    expect(TOP20_LIMIT).toBe(20);
    expect(TOP20_MIN_SERIES).toBeGreaterThanOrEqual(2);
  });
});

describe("benchmarkForMic", () => {
  it("is case- and whitespace-tolerant", () => {
    expect(benchmarkForMic("xnas")?.indexSymbol).toBe("^IXIC");
    expect(benchmarkForMic("  XPAR ")?.indexLabel).toBe("CAC 40");
  });
  it("returns null for unknown MICs (never guesses)", () => {
    expect(benchmarkForMic("BOGUS")).toBeNull();
    expect(benchmarkForMic("")).toBeNull();
    expect(benchmarkForMic(null)).toBeNull();
    expect(benchmarkForMic(undefined)).toBeNull();
  });
});

describe("cache keys (future user_id/tier namespaced, guest/free today)", () => {
  it("defaults to guest/free with no auth", () => {
    expect(aspiCacheKey("XNAS", "1d")).toEqual(["aspi", "XNAS", "1d", "u:guest", "t:free"]);
    expect(aspiInflightKey("XNAS", "1d")).toBe("aspi:XNAS:1d:guest:free");
    expect(top20CacheKey("XPAR")).toEqual(["aspi-top20", "XPAR", "u:guest", "t:free"]);
    expect(top20InflightKey("XPAR")).toBe("aspi-top20:XPAR:guest:free");
  });
  it("namespaces future user/tier without changing shape", () => {
    expect(aspiCacheKey("XNAS", "1wk", "u123", "pro")).toEqual(["aspi", "XNAS", "1wk", "u:u123", "t:pro"]);
    expect(aspiInflightKey("XNAS", "1wk", "u123", "pro")).toBe("aspi:XNAS:1wk:u123:pro");
    expect(top20CacheKey("XPAR", "u123", "pro")[2]).toBe("u:u123");
  });
  it("falls back to 1d on unknown timeframes", () => {
    expect(aspiCacheKey("XNAS", "5m")[2]).toBe("1d");
    expect(ASPI_TIMEFRAMES).toEqual(["1d", "1wk", "1mo"]);
  });
});

describe("closesFromCandles", () => {
  it("maps closes, sorts ascending, dedupes by time", () => {
    const out = closesFromCandles([
      { time: "2026-01-15", open: 1, high: 2, low: 0.5, close: 102 },
      { time: "2026-01-13", open: 1, high: 2, low: 0.5, close: 100 },
      { time: "2026-01-15", open: 1, high: 2, low: 0.5, close: 103 },
      { time: "2026-01-14", open: 1, high: 2, low: 0.5, close: 101 },
    ]);
    expect(out).toEqual([
      { t: "2026-01-13", close: 100 },
      { t: "2026-01-14", close: 101 },
      { t: "2026-01-15", close: 103 },
    ]);
  });
  it("drops invalid rows instead of zero-filling", () => {
    const out = closesFromCandles([
      { time: "2026-01-15", close: 10 },
      { time: null, close: 10 },
      { time: "2026-01-14", close: null },
      { time: "2026-01-13", close: "NaN" },
      { time: "", close: 5 },
      "junk",
      null,
    ]);
    expect(out).toEqual([{ t: "2026-01-15", close: 10 }]);
  });
  it("returns [] for non-array input", () => {
    expect(closesFromCandles(null)).toEqual([]);
    expect(closesFromCandles(undefined)).toEqual([]);
    expect(closesFromCandles({})).toEqual([]);
  });
});

describe("normalizeAspiSeries", () => {
  it("normalizes a canonical bars payload", () => {
    const out = normalizeAspiSeries({
      mic: "xnas",
      label: "Nasdaq — Nasdaq Composite",
      symbol: "QQQ",
      usedSymbol: "QQQ",
      isProxy: true,
      timeframe: "1d",
      candles: [
        { time: "2026-01-14", close: 500 },
        { time: "2026-01-15", close: 510 },
      ],
      provenance: prov(),
    });
    expect(out.mic).toBe("XNAS");
    expect(out.usedSymbol).toBe("QQQ");
    expect(out.isProxy).toBe(true);
    expect(out.points).toHaveLength(2);
    expect(out.start).toBe("2026-01-14");
    expect(out.end).toBe("2026-01-15");
    expect(out.lastClose).toBe(510);
    expect(out.count).toBe(2);
    expect(out.fallback_used).toBe(false);
  });
  it("throws on missing provenance (fail-closed, never fabricates)", () => {
    expect(() =>
      normalizeAspiSeries({ mic: "XSHG", candles: [{ time: "2026-01-15", close: 3000 }] })
    ).toThrow("provenance missing");
    expect(() => normalizeAspiSeries({ mic: "XSHG", candles: [] })).toThrow("provenance missing");
  });
});

describe("rebaseSeries (base 100)", () => {
  it("scales the first close to 100", () => {
    expect(
      rebaseSeries([
        { t: "2026-01-13", close: 100 },
        { t: "2026-01-14", close: 110 },
        { t: "2026-01-15", close: 90 },
      ])
    ).toEqual([
      { t: "2026-01-13", value: 100 },
      { t: "2026-01-14", value: 110 },
      { t: "2026-01-15", value: 90 },
    ]);
  });
  it("guards empty and zero-base input (never divides by zero)", () => {
    expect(rebaseSeries([])).toEqual([]);
    expect(rebaseSeries(null)).toEqual([]);
    expect(
      rebaseSeries([
        { t: "2026-01-13", close: 0 },
        { t: "2026-01-14", close: 5 },
      ])
    ).toEqual([]);
  });
});

describe("computeEqualWeightedIndex", () => {
  function series(sym, closes, dates = ["2026-01-13", "2026-01-14", "2026-01-15"]) {
    return { symbol: sym, points: dates.map((t, i) => ({ t, close: closes[i] })) };
  }
  it("averages rebased closes over the union of dates", () => {
    const out = computeEqualWeightedIndex([
      series("A", [100, 110, 120]),
      series("B", [200, 200, 200]),
      series("C", [50, 55, 60]),
    ]);
    expect(out.reason).toBeNull();
    expect(out.constituentsUsed).toBe(3);
    expect(out.points).toHaveLength(3);
    // Day 1: all rebased to 100 -> mean 100.
    expect(out.points[0].value).toBeCloseTo(100, 9);
    // Day 2: (110 + 100 + 110) / 3 (6dp-rounded composite).
    expect(out.points[1].value).toBeCloseTo((110 + 100 + 110) / 3, 4);
    expect(out.methodology).toContain("Equal-weighted");
  });
  it("lets a constituent contribute only on dates it traded (no fill)", () => {
    const out = computeEqualWeightedIndex([
      series("A", [100, 110]),
      series("B", [100, 110]),
      { symbol: "C", points: [{ t: "2026-01-14", close: 50 }] },
    ]);
    expect(out.reason).toBeNull();
    expect(out.points).toHaveLength(2);
    expect(out.points[0].value).toBeCloseTo(100, 9);
  });
  it("refuses thin composites with an honest reason (never a fake line)", () => {
    const thin = computeEqualWeightedIndex([series("A", [100, 110, 120]), series("B", [200, 200, 200])]);
    expect(thin.points).toEqual([]);
    expect(thin.constituentsUsed).toBe(2);
    expect(thin.reason).toContain("need 3");
    const single = computeEqualWeightedIndex([
      { symbol: "A", points: [{ t: "2026-01-15", close: 1 }] },
      { symbol: "B", points: [{ t: "2026-01-15", close: 2 }] },
      { symbol: "C", points: [{ t: "2026-01-15", close: 3 }] },
    ]);
    expect(single.points).toEqual([]);
    expect(single.reason).toContain("fewer than 2");
  });
});

describe("normalizeTop20", () => {
  function liqRow(sym, i) {
    return { symbol: sym, price: 100 + i, change_pct: i - 10, volume: 1000 * (i + 1), turnover: 1e6 * (i + 1), range_pct: 1.5, market_state: "open" };
  }
  it("prefers liquidity rows and slices to 20 in order", () => {
    const liquidityRows = Array.from({ length: 30 }, (_, i) => liqRow(`S${i}`, i));
    const out = normalizeTop20({ mic: "XNYS", liquidityRows });
    expect(out.methodology).toBe("liquidity-turnover");
    expect(out.rows).toHaveLength(20);
    expect(out.rows[0].symbol).toBe("S0");
    expect(out.rows[19].symbol).toBe("S19");
    expect(out.reason).toBeNull();
  });
  it("falls back to screener rank order, honestly labelled", () => {
    const out = normalizeTop20({
      mic: "XPAR",
      screenerRows: [
        { symbol: "MC.PA", company_name: "LVMH", currency: "EUR", price: 700, change_pct: 0.5, market_state: "open" },
      ],
    });
    expect(out.methodology).toBe("screener-rank-fallback");
    expect(out.rows).toHaveLength(1);
    expect(out.rows[0].currency).toBe("EUR");
    expect(out.methodologyNote).toContain("NOT by size");
  });
  it("returns empty + reason when both sources are missing (never invents)", () => {
    for (const input of [{ mic: "XBRU" }, { mic: "XBRU", liquidityRows: [], screenerRows: [] }, null, undefined]) {
      const out = normalizeTop20(input, "XBRU");
      expect(out.rows).toEqual([]);
      expect(out.methodology).toBe("unavailable");
      expect(out.reason).toContain("never fabricated");
    }
  });
});

describe("enrichConstituentsWithScreener", () => {
  it("fills company/currency gaps without overwriting", () => {
    const out = enrichConstituentsWithScreener(
      [
        { symbol: "MC.PA", company_name: null, currency: null, price: 700 },
        { symbol: "ACA.PA", company_name: "Keep Me", currency: "EUR", price: 14 },
      ],
      [
        { symbol: "mc.pa", company_name: "LVMH", currency: "eur" },
        { symbol: "ACA.PA", company_name: "Overwrite?", currency: "USD" },
      ]
    );
    expect(out[0].company_name).toBe("LVMH");
    expect(out[0].currency).toBe("EUR");
    expect(out[1].company_name).toBe("Keep Me");
    expect(out[1].currency).toBe("EUR");
  });
  it("leaves unknown symbols untouched (no guessing)", () => {
    const out = enrichConstituentsWithScreener([{ symbol: "ZZZ", company_name: null, currency: null }], []);
    expect(out[0].company_name).toBeNull();
    expect(out[0].currency).toBeNull();
  });
});

describe("normalizeNativeIndexSeries (GET /api/markets/{mic}/index contract)", () => {
  it("converts native points to candles without inventing range", async () => {
    const { normalizeNativeIndexSeries } = await import("./aspi");
    const out = normalizeNativeIndexSeries(
      {
        mic: "XSHG",
        label: "SSE — SSE Composite",
        symbol: "000001.SS",
        used_symbol: "000001.SS",
        is_proxy: false,
        timeframe: "1d",
        points: [
          { t: "2026-01-14", close: 3000 },
          { t: "2026-01-15", close: 3050 },
        ],
        provenance: prov(),
      },
      "XSHG"
    );
    expect(out.mic).toBe("XSHG");
    expect(out.count).toBe(2);
    expect(out.lastClose).toBe(3050);
    expect(out.isProxy).toBe(false);
  });
  it("empty points -> zero-count series (never fabricates)", async () => {
    const { normalizeNativeIndexSeries } = await import("./aspi");
    const out = normalizeNativeIndexSeries({ mic: "XNYS", points: [], provenance: prov() }, "XNYS");
    expect(out.count).toBe(0);
    expect(out.lastClose).toBeNull();
  });
  it("missing provenance throws (fail-closed)", async () => {
    const { normalizeNativeIndexSeries } = await import("./aspi");
    expect(() => normalizeNativeIndexSeries({ mic: "XNYS", points: [{ t: "2026-01-15", close: 100 }] }, "XNYS")).toThrow(
      "provenance missing"
    );
  });
});

describe("cap-weighted Top-20 (market_cap opt-in, turnover never used)", () => {
  it("cap-weights when every constituent carries a finite market_cap", async () => {
    const { computeEqualWeightedIndex } = await import("./aspi");
    const mk = (sym, closes) => ({ symbol: sym, points: closes.map((c, i) => ({ t: `2026-01-1${i + 1}`, close: c })) });
    const out = computeEqualWeightedIndex([mk("A", [100, 110]), mk("B", [200, 220]), mk("C", [50, 55])], {
      weights: { A: 1000, B: 3000, C: 2000 },
    });
    expect(out.weighting).toBe("cap-weighted");
    expect(out.points).toHaveLength(2);
  });
  it("falls back to equal-weighted when any cap is missing", async () => {
    const { computeEqualWeightedIndex } = await import("./aspi");
    const mk = (sym, closes) => ({ symbol: sym, points: closes.map((c, i) => ({ t: `2026-01-1${i + 1}`, close: c })) });
    const out = computeEqualWeightedIndex([mk("A", [100, 110]), mk("B", [200, 220]), mk("C", [50, 55])], {
      weights: { A: 1000, B: 3000 },
    });
    expect(out.weighting).toBe("equal-weighted");
    expect(out.reason).toMatch(/market-cap/i);
  });
});

describe("combineAspiProvenance", () => {
  it("merges envelopes: oldest as_of, joined sources, max delay", () => {
    const out = combineAspiProvenance([
      prov({ source: "yfinance", as_of: TS, delay_minutes: 15, quality_grade: "B" }),
      prov({ source: "akshare", as_of: TS_OLD, delay_minutes: 5, quality_grade: "A" }),
    ]);
    expect(out.source).toBe("akshare+yfinance");
    expect(out.as_of).toBe(new Date(TS_OLD).toISOString());
    expect(out.delay_minutes).toBe(15);
    expect(out.quality_grade).toBe("B");
    expect(out.fallback_used).toBe(false);
  });
  it("is sticky on fallback and forces grade D", () => {
    const out = combineAspiProvenance([
      prov({ quality_grade: "A", delay_minutes: 0 }),
      prov({ source: "stub", fallback_used: true, quality_grade: "A", missing_fields: ["volume"] }),
    ]);
    expect(out.fallback_used).toBe(true);
    expect(out.quality_grade).toBe("D");
    expect(out.missing_fields).toContain("volume");
  });
  it("returns an honest empty envelope with no entries (non-fallback)", () => {
    const out = combineAspiProvenance([]);
    expect(out.fallback_used).toBe(false);
    expect(out.delay_minutes).toBe(15);
    expect(out.quality_grade).toBe("U");
    expect(out.missing_fields).toContain("provenance");
  });
});
