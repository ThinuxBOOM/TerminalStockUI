import { describe, expect, it } from "vitest";
import { normalizeAIHealthTest, normalizeBarsToCandles, normalizeBarTime } from "./client";
const TS = "2026-01-15T12:00:00.000Z";
function prov(over = {}) {
  return {
    source: "yfinance",
    as_of: TS,
    delay_minutes: 15,
    quality_grade: "C",
    fallback_used: true,
    missing_fields: [],
    ...over
  };
}
function bar(ts, o, h, l, c) {
  return { ts, open: o, high: h, low: l, close: c, volume: 1e3, missing_fields: [] };
}
describe("normalizeBarTime", () => {
  it("keeps YYYY-MM-DD and truncates ISO datetimes to the day", () => {
    expect(normalizeBarTime("2026-01-15")).toBe("2026-01-15");
    expect(normalizeBarTime("2026-01-15T00:00:00+00:00")).toBe("2026-01-15");
  });
  it("converts epoch seconds/milliseconds to YYYY-MM-DD", () => {
    expect(normalizeBarTime(1768435200)).toBe("2026-01-15");
    expect(normalizeBarTime(17684352e5)).toBe("2026-01-15");
  });
  it("returns null for unparseable input (never guesses)", () => {
    expect(normalizeBarTime("not-a-date")).toBeNull();
    expect(normalizeBarTime("")).toBeNull();
    expect(normalizeBarTime(null)).toBeNull();
    expect(normalizeBarTime(void 0)).toBeNull();
    expect(normalizeBarTime({})).toBeNull();
  });
});
describe("normalizeBarsToCandles (GET /api/market_data/bars contract)", () => {
  it("extracts candles from the canonical backend shape", () => {
    const out = normalizeBarsToCandles(
      {
        symbol: "AAPL",
        timeframe: "1d",
        bars: [bar("2026-01-14T00:00:00+00:00", 100, 102, 99, 101), bar("2026-01-15", 101, 103, 100, 102)],
        provenance: prov()
      },
      "AAPL",
      "1d"
    );
    expect(out.symbol).toBe("AAPL");
    expect(out.timeframe).toBe("1d");
    expect(out.candles).toEqual([
      { time: "2026-01-14", open: 100, high: 102, low: 99, close: 101 },
      { time: "2026-01-15", open: 101, high: 103, low: 100, close: 102 }
    ]);
    expect(out.provenance.source).toBe("yfinance");
    expect(out.provenance.fallback_used).toBe(true);
  });
  it("accepts alias keys (data/candles, time/date) without inventing rows", () => {
    const out = normalizeBarsToCandles(
      {
        ticker: "MC.PA",
        data: [{ time: "2026-01-15", open: "10", high: 11, low: 9, close: 10.5 }],
        provenance: prov({ source: "fx-free", fallback_used: false })
      },
      "MC.PA",
      "1d"
    );
    expect(out.symbol).toBe("MC.PA");
    expect(out.candles).toHaveLength(1);
    expect(out.candles[0]).toEqual({ time: "2026-01-15", open: 10, high: 11, low: 9, close: 10.5 });
  });
  it("drops invalid rows instead of zero-filling", () => {
    const out = normalizeBarsToCandles(
      {
        symbol: "X",
        bars: [
          bar("2026-01-15", 1, 2, 0.5, 1.5),
          { ts: "bogus", open: 1, high: 2, low: 0.5, close: 1.5 },
          { ts: "2026-01-14", open: null, high: 2, low: 0.5, close: 1.5 },
          { ts: "2026-01-13", open: 1, high: "NaN", low: 0.5, close: 1.5 },
          "junk-row",
          null
        ],
        provenance: prov()
      },
      "X"
    );
    expect(out.candles).toEqual([{ time: "2026-01-15", open: 1, high: 2, low: 0.5, close: 1.5 }]);
  });
  it("yields zero candles with stale-marked provenance on empty/unparseable payloads", () => {
    const empty = normalizeBarsToCandles({ symbol: "X", bars: [], provenance: prov() }, "X");
    expect(empty.candles).toEqual([]);
    expect(empty.provenance.fallback_used).toBe(true);
    for (const raw of [{ symbol: "X", bars: [] }, {}, null, void 0]) {
      const out = normalizeBarsToCandles(raw, "X");
      expect(out.candles).toEqual([]);
      expect(out.symbol).toBe("X");
      expect(out.provenance.fallback_used).toBe(true);
      expect(out.provenance.missing_fields).toContain("provenance");
    }
  });
});
describe("normalizeAIHealthTest (POST /api/ai/providers/health/test contract)", () => {
  it("passes {ok} shapes through", () => {
    expect(normalizeAIHealthTest({ ok: true, latency_ms: 12, message: "probe recorded" }, "gemini")).toEqual({
      ok: true,
      latency_ms: 12,
      message: "probe recorded",
      provider: "gemini"
    });
  });
  it("maps configured=false to FAIL with an honest key-missing message (never silent pass)", () => {
    const out = normalizeAIHealthTest(
      { providers: [{ provider: "gemini", model: "gemini-3.7-flash", configured: false, stub_mode: true }] },
      "gemini"
    );
    expect(out?.ok).toBe(false);
    expect(out?.message).toContain("API key missing");
    expect(out?.provider).toBe("gemini");
  });
  it("maps configured=true to OK and names the model", () => {
    const out = normalizeAIHealthTest(
      { providers: [{ provider: "openai", model: "gpt-x", configured: true, stub_mode: false }] },
      "openai"
    );
    expect(out?.ok).toBe(true);
    expect(out?.message).toContain("gpt-x");
  });
  it("returns null when the payload carries no recognizable shape", () => {
    expect(normalizeAIHealthTest({}, "gemini")).toBeNull();
    expect(normalizeAIHealthTest({ providers: [] }, "gemini")).toBeNull();
    expect(normalizeAIHealthTest(null, "gemini")).toBeNull();
  });
});
describe("indicator request helpers (timeframes + favorites stub)", () => {
  it("TIMEFRAME_PRESETS wires 1D/1W/1M/3M/1Y/2Y/5Y to bars-API limits within the backend cap", async () => {
    const { TIMEFRAME_PRESETS, BARS_MAX_LIMIT, BARS_BACKEND_CAP, resolveTimeframePreset } = await import("./client");
    expect(TIMEFRAME_PRESETS.map((p) => p.id)).toEqual(["1D", "1W", "1M", "3M", "1Y", "2Y", "5Y"]);
    expect(TIMEFRAME_PRESETS.map((p) => p.limit)).toEqual([5, 7, 30, 90, 250, 500, 1000]);
    expect(BARS_MAX_LIMIT).toBe(1000);
    expect(BARS_BACKEND_CAP).toBe(1000);
    expect(resolveTimeframePreset("1y").limit).toBe(250);
    expect(resolveTimeframePreset("5y").limit).toBe(1000);
    expect(resolveTimeframePreset("bogus").id).toBe("3M");
  });
  it("normalizeIndicatorList canonicalizes aliases, dedupes, drops unknowns", async () => {
    const { normalizeIndicatorList, buildIndicatorsParam } = await import("./client");
    expect(normalizeIndicatorList(["sma20", "SMA-50", "ema12", "RSI", "macd", "bb", "vwap", "atr", "bogus", "SMA20"])).toEqual(
      ["SMA20", "SMA50", "EMA12", "RSI14", "MACD", "BB20", "VWAP", "ATR14"]
    );
    expect(normalizeIndicatorList("SMA20, ema26")).toEqual(["SMA20", "EMA26"]);
    expect(buildIndicatorsParam(["SMA20", "RSI14"])).toBe("SMA20,RSI14");
    expect(buildIndicatorsParam([])).toBeUndefined();
  });
  it("per-user favorites stub namespaces by future user_id (guest fallback, node-safe)", async () => {
    const { favoriteIndicatorsKey, loadFavoriteIndicators, saveFavoriteIndicators } = await import("./client");
    expect(favoriteIndicatorsKey(undefined)).toBe("indicators:guest");
    expect(favoriteIndicatorsKey("u123")).toBe("indicators:u123");
    expect(loadFavoriteIndicators(undefined, ["SMA20"])).toEqual(["SMA20"]);
    expect(saveFavoriteIndicators(undefined, ["SMA20"])).toBe(false);
  });
});
describe("normalizeIndicators (GET /api/analytics?indicators=... contract)", () => {
  it("passes canonical per-indicator point arrays through (time-normalized, sorted)", async () => {
    const { normalizeIndicators } = await import("./client");
    const out = normalizeIndicators({
      symbol: "AAPL",
      indicators: {
        SMA20: [
          { time: "2026-01-15", value: 102 },
          { ts: "2026-01-14T00:00:00+00:00", value: "101.5" },
        ],
        RSI14: [{ date: "2026-01-15", value: 62.5 }],
      },
    });
    expect(out.SMA20).toEqual([
      { time: "2026-01-14", value: 101.5 },
      { time: "2026-01-15", value: 102 },
    ]);
    expect(out.RSI14).toEqual([{ time: "2026-01-15", value: 62.5 }]);
  });
  it("expands BB20 upper/middle/lower and MACD line/signal/histogram objects", async () => {
    const { normalizeIndicators } = await import("./client");
    const out = normalizeIndicators({
      indicators: {
        BB20: {
          upper: [{ time: "2026-01-15", value: 110 }],
          middle: [{ time: "2026-01-15", value: 100 }],
          lower: [{ time: "2026-01-15", value: 90 }],
        },
        MACD: {
          macd: [{ time: "2026-01-15", value: 1.2 }],
          signal: [{ time: "2026-01-15", value: 1 }],
          histogram: [{ time: "2026-01-15", value: 0.2 }],
        },
      },
    });
    expect(out.BB_UPPER).toEqual([{ time: "2026-01-15", value: 110 }]);
    expect(out.BB_MIDDLE).toEqual([{ time: "2026-01-15", value: 100 }]);
    expect(out.BB_LOWER).toEqual([{ time: "2026-01-15", value: 90 }]);
    expect(out.MACD_LINE).toEqual([{ time: "2026-01-15", value: 1.2 }]);
    expect(out.MACD_SIGNAL).toEqual([{ time: "2026-01-15", value: 1 }]);
    expect(out.MACD_HIST).toEqual([{ time: "2026-01-15", value: 0.2 }]);
  });
  it("drops null/NaN values and bad times instead of zero-filling", async () => {
    const { normalizeIndicators } = await import("./client");
    const out = normalizeIndicators({
      indicators: {
        SMA20: [
          { time: "2026-01-15", value: null },
          { time: "bogus", value: 5 },
          { time: "2026-01-14", value: "NaN" },
          { time: "2026-01-13", value: 99 },
        ],
      },
    });
    expect(out.SMA20).toEqual([{ time: "2026-01-13", value: 99 }]);
  });
  it("returns {} for snapshot-only payloads (technical latest-values are not plottable series)", async () => {
    const { normalizeIndicators } = await import("./client");
    expect(
      normalizeIndicators({ symbol: "AAPL", technical: { sma_20: { value: { kind: "series", latest: 100 } } } })
    ).toEqual({});
    expect(normalizeIndicators(null)).toEqual({});
    expect(normalizeIndicators({})).toEqual({});
  });
});
