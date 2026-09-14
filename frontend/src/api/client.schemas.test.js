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
