import { describe, expect, it } from "vitest";
import {
  liquidationCacheKey,
  normalizeLiquidationProxy,
  normalizeLiquidationSort,
} from "./liquidation";

describe("normalizeLiquidationSort", () => {
  it("accepts intensity|symbol, defaults to intensity", () => {
    expect(normalizeLiquidationSort("intensity")).toBe("intensity");
    expect(normalizeLiquidationSort("symbol")).toBe("symbol");
    expect(normalizeLiquidationSort("bogus")).toBe("intensity");
    expect(normalizeLiquidationSort(undefined)).toBe("intensity");
  });
});

describe("normalizeLiquidationProxy (GET /api/markets/{mic}/liquidation-proxy contract)", () => {
  it("normalizes rows + aggregates + provenance", () => {
    const out = normalizeLiquidationProxy(
      {
        mic: "XNYS",
        rows: [
          { symbol: "AAPL", side: "short_proxy", intensity: 3.5, vol_z: 2.8, range_atr: 1.2, volume_anomaly: true },
          { symbol: "JPM", side: "long_proxy", intensity: 0, vol_z: 0.5, range_atr: 0.8, volume_anomaly: false },
        ],
        aggregates: { n: 2, skipped: 1, long_proxy_n: 1, short_proxy_n: 1, mean_intensity: 1.75, max_intensity: 3.5 },
        methodology: "intensity = ...",
        disclosure: "PROXY — ...",
        provenance: {
          source: "scan",
          as_of: "2026-01-15T12:00:00.000Z",
          delay_minutes: 15,
          quality_grade: "C",
          fallback_used: false,
          missing_fields: ["liquidation-feed"],
        },
      },
      "XNYS"
    );
    expect(out.mic).toBe("XNYS");
    expect(out.rows).toHaveLength(2);
    expect(out.rows[0].symbol).toBe("AAPL");
    expect(out.aggregates.n).toBe(2);
    expect(out.provenance.missing_fields).toContain("liquidation-feed");
  });

  it("empty in -> empty out (never fabricates rows)", () => {
    const out = normalizeLiquidationProxy({ mic: "XNAS", rows: [] }, "XNAS");
    expect(out.rows).toEqual([]);
    expect(out.aggregates.n).toBe(0);
    expect(normalizeLiquidationProxy(null, "XNAS").rows).toEqual([]);
    expect(normalizeLiquidationProxy({}, "").mic).toBe("UNKNOWN");
  });

  it("cache keys are mic+sort+limit scoped", () => {
    expect(liquidationCacheKey("xnys", { sort: "symbol", limit: 10 })).toEqual([
      "market-liquidation",
      "XNYS",
      "symbol",
      10,
    ]);
  });
});
