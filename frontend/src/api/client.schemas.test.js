import { describe, expect, it } from "vitest";
import { RankResponseSchema } from "./client";
const TS = "2026-01-15T12:00:00.000Z";
function prov(over = {}) {
  return {
    source: "stub",
    as_of: TS,
    delay_minutes: 5,
    quality_grade: "A",
    fallback_used: false,
    missing_fields: [],
    ...over
  };
}
describe("RankResponseSchema (normalizeRank contract)", () => {
  it("passes ranking through with a null fx_provenance", () => {
    const parsed = RankResponseSchema.parse({
      target_ccy: "USD",
      ranking: [{ symbol: "AAPL", provenance: prov({ source: "market-data-api" }) }],
      fx_provenance: null
    });
    expect(parsed.target_ccy).toBe("USD");
    expect(parsed.ranking).toHaveLength(1);
    expect(parsed.ranking[0]?.symbol).toBe("AAPL");
    expect(parsed.fx_provenance).toBeNull();
  });
  it("preserves a solid fx_provenance envelope", () => {
    const parsed = RankResponseSchema.parse({
      target_ccy: "EUR",
      ranking: [],
      fx_provenance: prov({ source: "fx-api" })
    });
    expect(parsed.fx_provenance?.source).toBe("fx-api");
  });
});
describe("normalizeRank (backend `ranked` wire key)", () => {
  it("reads the backend canonical `ranked` key, not just aliases", async () => {
    const { normalizeRank } = await import("./client");
    const out = normalizeRank(
      {
        target_ccy: "USD",
        ranked: [{ symbol: "MC.PA", converted: 772.74, provenance: prov({ source: "fx" }) }]
      },
      ["MC.PA"],
      "USD"
    );
    expect(out.ranking).toHaveLength(1);
    expect(out.ranking[0]?.symbol).toBe("MC.PA");
  });
  it("still honors the legacy `ranking` alias", async () => {
    const { normalizeRank } = await import("./client");
    const out = normalizeRank(
      { target_ccy: "USD", ranking: [{ symbol: "AAPL" }] },
      ["AAPL"],
      "USD"
    );
    expect(out.ranking).toHaveLength(1);
  });
});
describe("normalizeAIOpinion safe degrade + token/latency meta", () => {
  it("tryNormalizeAIOpinion returns null for malformed AI (never throws)", async () => {
    const { tryNormalizeAIOpinion, normalizeAIOpinion } = await import("./client");
    expect(tryNormalizeAIOpinion(null)).toBeNull();
    expect(tryNormalizeAIOpinion({ direction: "bullish" })).toBeNull();
    expect(() => normalizeAIOpinion({ direction: "bullish" })).toThrow();
  });
  it("preserves provider/model/evidence plus token/latency meta", async () => {
    const { normalizeAIOpinion } = await import("./client");
    const out = normalizeAIOpinion({
      direction: "bullish",
      probability: 0.7,
      time_horizon_days: 21,
      evidence_ids: ["trend_21d"],
      provider: "openai",
      model: "gpt-4o",
      latency_ms: 1234,
      usage: { total_tokens: 567 },
      provenance: prov(),
    });
    expect(out.provider).toBe("openai");
    expect(out.model).toBe("gpt-4o");
    expect(out.evidence_ids).toEqual(["trend_21d"]);
    expect(out.latency_ms).toBe(1234);
    expect(out.tokens).toBe(567);
  });
  it("still rejects evidence-less opinions (Rejected grade)", async () => {
    const { normalizeAIOpinion } = await import("./client");
    expect(() =>
      normalizeAIOpinion({ direction: "bullish", probability: 0.7, time_horizon_days: 21 })
    ).toThrow();
  });
});
describe("IndicatorPointSchema (overlay point contract)", () => {
  it("accepts {time, value} and nullable values (gaps, never zero-filled)", async () => {
    const { IndicatorPointSchema } = await import("./client");
    expect(IndicatorPointSchema.parse({ time: "2026-01-15", value: 101.5 })).toMatchObject({
      time: "2026-01-15",
      value: 101.5,
    });
    expect(IndicatorPointSchema.parse({ time: "2026-01-15", value: null }).value).toBeNull();
    expect(() => IndicatorPointSchema.parse({ value: 1 })).toThrow();
  });
});
describe("normalizeAnalytics with ?indicators (overlay series passthrough)", () => {
  it("attaches normalized indicators + requested list while preserving the snapshot", async () => {
    const { normalizeAnalytics } = await import("./client");
    const out = normalizeAnalytics(
      {
        symbol: "AAPL",
        technical: { sma_20: { value: 100 } },
        indicators: { SMA20: [{ time: "2026-01-15", value: 100 }] },
        provenance: prov(),
      },
      "AAPL",
      ["SMA20", "RSI14"]
    );
    expect(out.symbol).toBe("AAPL");
    expect(out.technical).toMatchObject({ sma_20: { value: 100 } });
    expect(out.requestedIndicators).toEqual(["SMA20", "RSI14"]);
    expect(out.indicators.SMA20).toEqual([{ time: "2026-01-15", value: 100 }]);
    expect(out.provenance.source).toBe("stub");
  });
  it("normalizeChart keeps header price and last candle from the same call", async () => {
    const { normalizeChart } = await import("./client");
    const out = normalizeChart(
      {
        symbol: "AAPL",
        timeframe: "1d",
        quote: {
          symbol: "AAPL",
          price: 333.28,
          currency: "USD",
          provenance: prov(),
        },
        bars: [
          { ts: "2026-09-16", open: 331, high: 332, low: 330, close: 333.28 },
        ],
        stitched: true,
        stitched_reason: null,
        provenance: prov(),
      },
      "AAPL",
      "1d"
    );
    expect(out.quote.price).toBe(333.28);
    expect(out.candles[out.candles.length - 1].close).toBe(out.quote.price);
    expect(out.stitched).toBe(true);
  });
  it("normalizeChart aligns the terminal print client-side on legacy backends", async () => {
    const { normalizeChart } = await import("./client");
    // Old backend: stitched=false but same-session quote present — the last
    // candle close is pulled to the header price (never appends: no open).
    const out = normalizeChart(
      {
        symbol: "AAPL",
        quote: { symbol: "AAPL", price: 333.28, currency: "USD", provenance: prov() },
        bars: [
          { ts: "2026-09-15", open: 330, high: 331, low: 329, close: 330.5 },
          { ts: "2026-01-15", open: 331, high: 332, low: 330, close: 331.5 },
        ],
        stitched: false,
        stitched_reason: "legacy-backend",
        provenance: prov(),
      },
      "AAPL"
    );
    expect(out.stitched).toBe(true);
    expect(out.candles[out.candles.length - 1].close).toBe(out.quote.price);
    // Newer-session quote: left alone client-side (no open to build a
    // forming bar with — the backend appends it when deployed).
    const newer = normalizeChart(
      {
        symbol: "AAPL",
        quote: { symbol: "AAPL", price: 340, currency: "USD",
                 provenance: prov({ as_of: "2026-09-17T13:30:00.000Z" }) },
        bars: [{ ts: "2026-09-15", open: 330, high: 331, low: 329, close: 330.5 }],
        stitched: false,
        provenance: prov(),
      },
      "AAPL"
    );
    expect(newer.stitched).toBe(false);
    expect(newer.candles).toHaveLength(1);
  });
  it("normalizeChart degrades honestly on quote problems (bars still render)", async () => {
    const { normalizeChart } = await import("./client");
    const missing = normalizeChart(
      {
        symbol: "AAPL",
        quote: null,
        bars: [{ ts: "2026-09-16", open: 1, high: 1, low: 1, close: 1 }],
        stitched: false,
        stitched_reason: "quote-missing",
        provenance: prov(),
      },
      "AAPL"
    );
    expect(missing.quote).toBeNull();
    expect(missing.candles).toHaveLength(1);
    expect(missing.stitched).toBe(false);
    // Bogus price never leaks NaN into the header — it normalizes to null.
    const bogus = normalizeChart(
      {
        symbol: "AAPL",
        quote: { symbol: "AAPL", price: "bogus", provenance: prov() },
        bars: [{ ts: "2026-09-16", open: 1, high: 1, low: 1, close: 1 }],
        stitched: false,
        provenance: prov(),
      },
      "AAPL"
    );
    expect(bogus.quote.price).toBeNull();
    expect(bogus.candles).toHaveLength(1);
  });
  it("yields empty indicators (never fabricated) when the backend sends snapshot-only", async () => {
    const { normalizeAnalytics } = await import("./client");
    const out = normalizeAnalytics(
      { symbol: "AAPL", technical: {}, provenance: prov() },
      "AAPL",
      ["MACD"]
    );
    expect(out.indicators).toEqual({});
    expect(out.requestedIndicators).toEqual(["MACD"]);
  });
  it("preserves the statements block so the note renders green when live, amber when not", async () => {
    const { normalizeAnalytics } = await import("./client");
    const live = normalizeAnalytics(
      {
        symbol: "AAPL",
        technical: {},
        note: "Statements: sec-edgar (FY2025, FY2024…)…",
        statements: { source: "sec-edgar", fiscal_ends: ["2025-09-27", "2024-09-28"] },
        provenance: prov(),
      },
      "AAPL"
    );
    expect(live.statements).toMatchObject({ source: "sec-edgar" });
    // Green/amber branches off statements.source (never the note text).
    expect(Boolean(live.statements?.source)).toBe(true);
    const down = normalizeAnalytics(
      {
        symbol: "AAPL",
        technical: {},
        note: "Statement feed not wired…",
        statements: { source: null, reason: "network down" },
        provenance: prov(),
      },
      "AAPL"
    );
    expect(Boolean(down.statements?.source)).toBe(false);
    const legacy = normalizeAnalytics(
      { symbol: "AAPL", technical: {}, provenance: prov() },
      "AAPL"
    );
    expect(legacy.statements).toBeNull();
  });
});
