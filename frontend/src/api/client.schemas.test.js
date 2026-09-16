import { describe, expect, it } from "vitest";
import { ForecastSchema, RankResponseSchema, ScreenerResponseSchema, ScreenerRowSchema } from "./client";
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
describe("ForecastSchema (normalizeForecast contract)", () => {
  it("fills backend-ensemble defaults: label, quality, provider, confidence", () => {
    const parsed = ForecastSchema.parse({ symbol: "AAPL", probability: 0.62, provenance: prov() });
    expect(parsed.label).toBe("");
    expect(parsed.quality_grade).toBe("U");
    expect(parsed.provider).toBe("deterministic-engine");
    expect(parsed.confidence).toBe("Unknown");
    expect(parsed.horizon_days).toBe(21);
    expect(parsed.why).toEqual([]);
    expect(parsed.risks).toEqual([]);
    expect(parsed.evidence_ids).toEqual([]);
    expect(parsed.calibration).toEqual([]);
    expect(parsed.limitations).toEqual([]);
  });
  it("preserves explicit backend values", () => {
    const parsed = ForecastSchema.parse({
      symbol: "AAPL",
      probability: 0.7,
      label: "Bullish",
      quality_grade: "A",
      provider: "ensemble-v3",
      horizon_days: 63,
      provenance: prov()
    });
    expect(parsed.label).toBe("Bullish");
    expect(parsed.probability).toBe(0.7);
    expect(parsed.quality_grade).toBe("A");
    expect(parsed.provider).toBe("ensemble-v3");
    expect(parsed.horizon_days).toBe(63);
  });
  it("rejects out-of-range probabilities", () => {
    expect(
      () => ForecastSchema.parse({ symbol: "AAPL", probability: 1.5, provenance: prov() })
    ).toThrow();
    expect(
      () => ForecastSchema.parse({ symbol: "AAPL", probability: -0.1, provenance: prov() })
    ).toThrow();
  });
  it("passes backend ensemble extras through (passthrough schema)", () => {
    const parsed = ForecastSchema.parse({
      symbol: "AAPL",
      probability: 0.62,
      direction_probability: 0.62,
      components: { momentum: 0.1 },
      provenance: prov()
    });
    expect(parsed.symbol).toBe("AAPL");
    const extra = parsed;
    expect(extra.direction_probability).toBe(0.62);
    expect(extra.components).toEqual({ momentum: 0.1 });
  });
});
describe("ScreenerRowSchema (normalizeScreenerRow contract)", () => {
  it("fills row defaults for a minimal backend row", () => {
    const parsed = ScreenerRowSchema.parse({
      symbol: "AAPL",
      direction_probability: 0.7,
      provenance: prov({ source: "screener-api" })
    });
    expect(parsed.company_name).toBe("");
    expect(parsed.exchange_mic).toBe("");
    expect(parsed.currency).toBe("USD");
    expect(parsed.confidence).toBe("Unknown");
    expect(parsed.model_version).toBe("");
    expect(parsed.horizons).toEqual([]);
    expect(parsed.price).toBeUndefined();
  });
  it("passes ranking rows through in order", () => {
    const rows = [
      { symbol: "AAA", direction_probability: 0.9, provenance: prov() },
      { symbol: "BBB", direction_probability: 0.8, provenance: prov() }
    ].map((r) => ScreenerRowSchema.parse(r));
    expect(rows.map((r) => r.symbol)).toEqual(["AAA", "BBB"]);
    expect(rows[0]?.direction_probability).toBe(0.9);
  });
});
describe("ScreenerResponseSchema (normalizeScreener contract)", () => {
  it("passes ranking through and preserves skipped entries", () => {
    const parsed = ScreenerResponseSchema.parse({
      results: [
        { symbol: "AAA", direction_probability: 0.9, provenance: prov() },
        { symbol: "BBB", direction_probability: 0.8, provenance: prov() }
      ],
      count: 2,
      universe_size: 10,
      skipped: [{ symbol: "CCC", reason: "no history" }],
      horizon: 21,
      disclosure: "Not investment advice."
    });
    expect(parsed.results).toHaveLength(2);
    expect(parsed.results?.map((r) => r.symbol)).toEqual(["AAA", "BBB"]);
    expect(parsed.skipped).toHaveLength(1);
    expect(parsed.skipped?.[0]?.symbol).toBe("CCC");
    expect(parsed.skipped?.[0]?.reason).toBe("no history");
    expect(parsed.count).toBe(2);
    expect(parsed.universe_size).toBe(10);
  });
  it("defaults an empty backend payload to empty ranking + zero counts", () => {
    const parsed = ScreenerResponseSchema.parse({});
    expect(parsed.results).toEqual([]);
    expect(parsed.count).toBe(0);
    expect(parsed.universe_size).toBe(0);
    expect(parsed.skipped).toEqual([]);
    expect(parsed.disclosure).toBe("");
  });
});
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
describe("ForecastSchema research split (deterministic vs AI)", () => {
  it("exposes quant/ai/blended/ai_weight with safe defaults", () => {
    const parsed = ForecastSchema.parse({ symbol: "AAPL", probability: 0.62, provenance: prov() });
    expect(parsed.quant_probability).toBeNull();
    expect(parsed.ai_probability).toBeNull();
    expect(parsed.blended_probability).toBeNull();
    expect(parsed.ai_weight).toBe(0);
    expect(parsed.direction).toBe("");
    expect(parsed.regime).toBeNull();
    expect(parsed.drawdown).toBeNull();
  });
  it("normalizeForecast keeps quant authoritative and clamps AI weight to 20%", async () => {
    const { normalizeForecast } = await import("./client");
    const out = normalizeForecast(
      {
        symbol: "AAPL",
        probability: 0.6,
        ai_probability: 0.9,
        ai_weight: 0.8,
        direction: "bullish",
        regime: "low-vol",
        drawdown: 0.05,
        why: ["a", "b", "c", "d", "e", "f"],
        risks: ["r1", "r2", "r3", "r4", "r5"],
        disclosure: "Not investment advice. custom",
        provenance: prov(),
      },
      "AAPL",
      21
    );
    expect(out.probability).toBe(0.6);
    expect(out.quant_probability).toBe(0.6);
    expect(out.ai_weight).toBe(0.2);
    expect(out.ai_probability).toBe(0.9);
    // blended = 0.8*0.6 + 0.2*0.9 = 0.66 — never overrides quant
    expect(out.blended_probability).toBeCloseTo(0.66, 10);
    expect(out.direction).toBe("bullish");
    expect(out.regime).toBe("low-vol");
    expect(out.drawdown).toBe(0.05);
    expect(out.why).toHaveLength(4);
    expect(out.risks).toHaveLength(4);
    expect(out.disclosure).toBe("Not investment advice. custom");
  });
  it("malformed AI degrades to quant alone (no override)", async () => {
    const { normalizeForecast } = await import("./client");
    const out = normalizeForecast(
      { symbol: "AAPL", probability: 0.55, ai_probability: 5, ai_weight: 0.2, provenance: prov() },
      "AAPL",
      21
    );
    expect(out.ai_probability).toBeNull();
    expect(out.blended_probability).toBe(0.55);
  });
});
describe("blended math + source labels", () => {
  it("blendProbs computes (1-w)*quant + w*ai and degrades safely", async () => {
    const { blendProbs, clampAIWeight } = await import("./client");
    expect(blendProbs(0.6, 0.9, 0.2)).toBeCloseTo(0.66, 10);
    expect(blendProbs(0.6, null, 0.2)).toBe(0.6);
    expect(blendProbs(0.6, 0.9, 0)).toBe(0.6);
    expect(blendProbs(null, 0.9, 0.2)).toBeNull();
    expect(clampAIWeight(0.8)).toBe(0.2);
    expect(clampAIWeight(0)).toBe(0);
    expect(clampAIWeight(-1)).toBe(0);
  });
  it("source labels mark deterministic vs AI vs disabled", async () => {
    const { sourceLabelForForecast, sourceLabelForAIOpinion, AI_DISABLED_LABEL } = await import("./client");
    expect(sourceLabelForForecast({})).toBe("SOURCE: DETERMINISTIC");
    expect(sourceLabelForAIOpinion({ provider: "openai" }, 0.2)).toBe("SOURCE: AI openai");
    expect(sourceLabelForAIOpinion({ provider: "openai" }, 0)).toBe(AI_DISABLED_LABEL);
    expect(AI_DISABLED_LABEL).toBe("AI DISABLED (ai_weight=0)");
  });
  it("auditForecastsUrl points at GET /api/audit/forecasts?symbol=", async () => {
    const { auditForecastsUrl } = await import("./client");
    expect(auditForecastsUrl("AAPL")).toBe("/api/audit/forecasts?symbol=AAPL&limit=20");
    expect(auditForecastsUrl("aapl", 5)).toBe("/api/audit/forecasts?symbol=AAPL&limit=5");
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
