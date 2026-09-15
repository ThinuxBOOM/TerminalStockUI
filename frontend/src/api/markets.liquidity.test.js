import { describe, expect, it } from "vitest";
import {
  MARKET_CURRENCIES,
  MARKET_MICS,
  TURNOVER_COMPARABILITY_NOTE,
  breadthRatios,
  canRankCrossCurrency,
  deriveMarketCardState,
  dominantMarketState,
  isStaleLiquidity,
  isXshgLunchWindow,
  normalizeLiquidityHistory,
  normalizeLiquidityHistoryWindow,
  normalizeMarketBreadth,
  normalizeMarketsOverview,
} from "./markets";

const TS = "2026-01-15T12:00:00.000Z";
function prov(over = {}) {
  return {
    source: "stub",
    as_of: TS,
    delay_minutes: 5,
    quality_grade: "B",
    fallback_used: false,
    missing_fields: [],
    ...over,
  };
}
function screenerRow(over = {}) {
  return {
    symbol: "AAA",
    price: 100,
    change_pct: 1.5,
    market_state: "open",
    volume: 1000,
    range_pct: 2.0,
    provenance: prov(),
    ...over,
  };
}

describe("market venue set (6 MICs, no hardcoded prices)", () => {
  it("covers XNYS/XNAS/XSHG/XPAR/XAMS/XBRU", () => {
    expect([...MARKET_MICS].sort()).toEqual(["XAMS", "XBRU", "XNAS", "XNYS", "XPAR", "XSHG"]);
  });
  it("maps native currencies (turnover stays native, no FX)", () => {
    expect(MARKET_CURRENCIES).toMatchObject({
      XNYS: "USD",
      XNAS: "USD",
      XSHG: "CNY",
      XPAR: "EUR",
      XAMS: "EUR",
      XBRU: "EUR",
    });
  });
  it("carries a comparability note and never embeds live prices", () => {
    expect(TURNOVER_COMPARABILITY_NOTE).toContain("no FX");
    expect(JSON.stringify(MARKET_MICS)).not.toMatch(/\d{3,}\.\d+/);
  });
});

describe("fail-closed provenance (no fallbacks)", () => {
  it("normalizeMarketBreadth throws on missing provenance (never synthesizes)", () => {
    expect(() => normalizeMarketBreadth({ mic: "XNYS" })).toThrow("provenance missing");
    expect(() => normalizeMarketBreadth({ mic: "XNYS", provenance: null })).toThrow("provenance missing");
    expect(() => normalizeMarketBreadth(null)).toThrow("provenance missing");
  });
  it("normalizeMarketsOverview throws on missing provenance", () => {
    expect(() => normalizeMarketsOverview({ markets: [] })).toThrow("provenance missing");
    expect(() => normalizeMarketsOverview(null)).toThrow("provenance missing");
  });
  it("normalizeLiquidityHistory throws on missing provenance", () => {
    expect(() => normalizeLiquidityHistory({ mic: "XNYS", points: [] }, "XNYS")).toThrow(
      "provenance missing"
    );
    expect(normalizeLiquidityHistory(null, "XNYS")).toBeNull();
  });
  it("never stands absolute change in for change_pct", () => {
    const out = normalizeMarketBreadth({
      mic: "XNYS",
      rows: [{ symbol: "Z", price: 50, change: 1.75, volume: 10, market_state: "open" }],
      provenance: prov(),
    });
    expect(out.rows[0].change_pct).toBeNull();
  });
});

describe("isStaleLiquidity (fallback/grade D -> stale)", () => {
  it("treats fallback_used or grade D as stale, never fakes fresh", () => {
    expect(isStaleLiquidity(prov({ fallback_used: true }))).toBe(true);
    expect(isStaleLiquidity(prov({ quality_grade: "D" }))).toBe(true);
    expect(isStaleLiquidity(prov({ quality_grade: "d" }))).toBe(true);
    expect(isStaleLiquidity(prov({ fallback_used: false, quality_grade: "B" }))).toBe(false);
    expect(isStaleLiquidity(null)).toBe(true);
    expect(isStaleLiquidity(undefined)).toBe(true);
    // {} carries no fallback flag and no grade D — not stale by contract
    // (stale = fallback_used OR grade D). Missing provenance (null) is stale.
    expect(isStaleLiquidity({})).toBe(false);
  });
});

describe("XSHG lunch window (11:30-13:00 Asia/Shanghai)", () => {
  // 04:00Z = 12:00 Shanghai (lunch); 01:30Z = 09:30 Shanghai (open);
  // 07:00Z = 15:00 Shanghai (closed). Weekday 2026-01-15 is a Thursday.
  it("detects lunch inside the window, not outside", () => {
    expect(isXshgLunchWindow(new Date("2026-01-15T04:00:00.000Z"))).toBe(true);
    expect(isXshgLunchWindow(new Date("2026-01-15T03:29:59.000Z"))).toBe(false);
    expect(isXshgLunchWindow(new Date("2026-01-15T01:30:00.000Z"))).toBe(false);
    expect(isXshgLunchWindow(new Date("2026-01-15T05:00:00.000Z"))).toBe(false);
  });
  it("does not call lunch on weekends", () => {
    // 2026-01-17 is a Saturday; noon Shanghai must not read as lunch.
    expect(isXshgLunchWindow(new Date("2026-01-17T04:00:00.000Z"))).toBe(false);
  });
});

describe("deriveMarketCardState (open/closed/lunch/delayed/stale)", () => {
  it("returns stale for fallback/grade-D provenance", () => {
    const m = {
      mic: "XNYS",
      market_state_counts: { open: 10 },
      provenance: prov({ fallback_used: true }),
    };
    expect(deriveMarketCardState(m)).toBe("stale");
  });
  it("picks the dominant explicit state", () => {
    const m = {
      mic: "XPAR",
      market_state_counts: { open: 8, closed: 2 },
      provenance: prov({ fallback_used: false, quality_grade: "B", delay_minutes: 5, as_of: new Date().toISOString() }),
    };
    expect(deriveMarketCardState(m)).toBe("open");
    expect(
      deriveMarketCardState({ ...m, market_state_counts: { closed: 9, open: 1 } })
    ).toBe("closed");
  });
  it("maps XSHG open inside lunch window to lunch", () => {
    const m = {
      mic: "XSHG",
      market_state_counts: { open: 5 },
      provenance: prov({ fallback_used: false, quality_grade: "B", delay_minutes: 5, as_of: new Date().toISOString() }),
    };
    expect(deriveMarketCardState(m, { now: new Date("2026-01-15T04:00:00.000Z") })).toBe("lunch");
    expect(deriveMarketCardState(m, { now: new Date("2026-01-15T01:30:00.000Z") })).toBe("open");
  });
  it("dominant helper ignores unknown keys", () => {
    expect(dominantMarketState({ unknown: 9 })).toBeNull();
    expect(dominantMarketState({ open: 2, bogus: 99 })).toBe("open");
    expect(dominantMarketState({})).toBeNull();
  });
});

describe("breadthRatios", () => {
  it("splits adv/dec/unch percentages that sum to 100", () => {
    const r = breadthRatios({ advancers: 1, decliners: 1, unchanged: 2, total: 4 });
    expect(r.advPct).toBeCloseTo(25, 10);
    expect(r.decPct).toBeCloseTo(25, 10);
    expect(r.advPct + r.decPct + r.unchPct).toBeCloseTo(100, 10);
  });
  it("returns zeros for empty markets (never NaN)", () => {
    expect(breadthRatios({ advancers: 0, decliners: 0, unchanged: 0, total: 0 })).toEqual({
      advPct: 0,
      decPct: 0,
      unchPct: 0,
      total: 0,
    });
  });
});

describe("normalizeMarketBreadth (currency passthrough)", () => {
  it("keeps native currency or falls back to venue default", () => {
    expect(normalizeMarketBreadth({ mic: "XSHG", currency: "CNY", provenance: prov() }).currency).toBe("CNY");
    expect(normalizeMarketBreadth({ mic: "XSHG", provenance: prov() }).currency).toBe("CNY");
    expect(normalizeMarketBreadth({ mic: "XNYS", provenance: prov() }).currency).toBe("USD");
  });
});

describe("liquidity history (future GET /api/markets/{mic}/liquidity/history)", () => {
  it("normalizes window enum, defaults to 1D", () => {
    expect(normalizeLiquidityHistoryWindow("5d")).toBe("5D");
    expect(normalizeLiquidityHistoryWindow("bogus")).toBe("1D");
    expect(normalizeLiquidityHistoryWindow(null)).toBe("1D");
  });
  it("normalizes points and sorts by t ascending", () => {
    const out = normalizeLiquidityHistory(
      {
        mic: "XNYS",
        window: "1D",
        points: [
          { t: "2026-01-15T15:00:00Z", turnover: 200, volume: 20, advancers: 5, decliners: 3, avg_change_pct: 0.2 },
          { t: "2026-01-15T14:00:00Z", turnover: 100, volume: 10, advancers: 4, decliners: 4, avg_change_pct: 0.1 },
        ],
        provenance: prov(),
      },
      "XNYS",
      "1D"
    );
    expect(out.mic).toBe("XNYS");
    expect(out.window).toBe("1D");
    expect(out.points.map((p) => p.t)).toEqual(["2026-01-15T14:00:00Z", "2026-01-15T15:00:00Z"]);
    expect(out.placeholder).toBe(false);
  });
  it("returns null for missing payloads and placeholder for empty points", () => {
    expect(normalizeLiquidityHistory(null, "XNYS")).toBeNull();
    expect(normalizeLiquidityHistory(undefined, "XNYS")).toBeNull();
    const empty = normalizeLiquidityHistory({ mic: "XNYS", points: [], provenance: prov() }, "XNYS");
    expect(empty.placeholder).toBe(true);
    expect(empty.points).toEqual([]);
  });
});

describe("canRankCrossCurrency (fresh-FX gate)", () => {
  const fresh = () => new Date().toISOString();
  it("forbids turnover ranking without fresh FX", () => {
    expect(canRankCrossCurrency(null)).toBe(false);
    expect(canRankCrossCurrency(prov({ fallback_used: true }))).toBe(false);
    expect(canRankCrossCurrency(prov({ quality_grade: "C", delay_minutes: 5, as_of: fresh() }))).toBe(false);
  });
  it("allows ranking only with fresh A/B FX provenance", () => {
    expect(canRankCrossCurrency(prov({ quality_grade: "A", delay_minutes: 5, as_of: fresh(), fallback_used: false }))).toBe(true);
    expect(canRankCrossCurrency(prov({ quality_grade: "B", delay_minutes: 0, as_of: fresh(), fallback_used: false }))).toBe(true);
  });
});
