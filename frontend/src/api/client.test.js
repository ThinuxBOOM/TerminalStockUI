import { describe, expect, it } from "vitest";
import { AI_TIMEOUT_MS, BACKTEST_TIMEOUT_MS, FORECAST_HORIZONS, MARKET_STATES, RANK_TIMEOUT_MS, SCREENER_TIMEOUT_MS, api, deriveMarketState, displaySymbol, freshnessOf, friendlyAIError, isFreshFxProvenance, isFxProvenanceMissingError, normalizeHealthProviders, normalizeMarketState, normalizeTargetCcy } from "./client";
const TS = "2026-01-15T12:00:00.000Z";
function prov(over = {}) {
  return {
    source: "stub",
    as_of: TS,
    delay_minutes: 0,
    quality_grade: "A",
    fallback_used: false,
    missing_fields: [],
    ...over
  };
}
describe("normalizeMarketState (case/whitespace/unknown)", () => {
  it("accepts exact lowercase states", () => {
    expect(normalizeMarketState("open")).toBe("open");
    expect(normalizeMarketState("closed")).toBe("closed");
    expect(normalizeMarketState("lunch")).toBe("lunch");
    expect(normalizeMarketState("delayed")).toBe("delayed");
    expect(normalizeMarketState("stale")).toBe("stale");
  });
  it("is case- and whitespace-tolerant", () => {
    expect(normalizeMarketState("OPEN")).toBe("open");
    expect(normalizeMarketState("  Closed  ")).toBe("closed");
    expect(normalizeMarketState("LUNCH")).toBe("lunch");
    expect(normalizeMarketState(" delayed ")).toBe("delayed");
    expect(normalizeMarketState("Stale")).toBe("stale");
  });
  it("returns null for unknown or non-string input", () => {
    expect(normalizeMarketState("bogus")).toBeNull();
    expect(normalizeMarketState("")).toBeNull();
    expect(normalizeMarketState("   ")).toBeNull();
    expect(normalizeMarketState("open!")).toBeNull();
    expect(normalizeMarketState(null)).toBeNull();
    expect(normalizeMarketState(void 0)).toBeNull();
    expect(normalizeMarketState(123)).toBeNull();
    expect(normalizeMarketState({})).toBeNull();
  });
});
describe("deriveMarketState (explicit-wins + provenance fallback)", () => {
  it("explicit API value wins over provenance", () => {
    expect(deriveMarketState(prov({ delay_minutes: 0 }), "closed")).toBe("closed");
    expect(deriveMarketState(prov({ delay_minutes: 0 }), "  LUNCH ")).toBe("lunch");
    expect(deriveMarketState(prov({ delay_minutes: 120 }), "open")).toBe("open");
  });
  it("falls back to provenance freshness when explicit is missing/unknown", () => {
    const fresh = () => (/* @__PURE__ */ new Date()).toISOString();
    expect(deriveMarketState(prov({ as_of: fresh(), delay_minutes: 0 }))).toBe("open");
    expect(deriveMarketState(prov({ as_of: fresh(), delay_minutes: 5 }))).toBe("delayed");
    expect(deriveMarketState(prov({ as_of: fresh(), delay_minutes: 0, fallback_used: true }))).toBe("delayed");
    expect(deriveMarketState(prov({ as_of: fresh(), delay_minutes: 120 }))).toBe("stale");
    expect(deriveMarketState(prov({ as_of: fresh(), delay_minutes: -1 }))).toBe("stale");
    expect(deriveMarketState(prov({ as_of: fresh(), delay_minutes: 0 }), "bogus")).toBe("open");
    expect(deriveMarketState(prov({ delay_minutes: 15 }))).toBe("stale");
  });
  it("never synthesizes closed/lunch from provenance alone", () => {
    const cases = [
      prov({ delay_minutes: 0 }),
      prov({ delay_minutes: 5 }),
      prov({ delay_minutes: 0, fallback_used: true }),
      prov({ delay_minutes: 120 })
    ];
    for (const p of cases) {
      expect(["closed", "lunch"]).not.toContain(deriveMarketState(p));
      expect(["closed", "lunch"]).not.toContain(deriveMarketState(p, "bogus"));
    }
  });
});
describe("displaySymbol (provider > exchange > symbol)", () => {
  it("prefers provider_symbol, then exchange_symbol, then symbol", () => {
    expect(
      displaySymbol({ symbol: "600519", provider_symbol: "600519.SS", exchange_symbol: "600519" })
    ).toBe("600519.SS");
    expect(displaySymbol({ symbol: "AAPL", exchange_symbol: "AAPL" })).toBe("AAPL");
    expect(displaySymbol({ symbol: "TSLA" })).toBe("TSLA");
  });
  it("treats blank provider/exchange as missing", () => {
    expect(displaySymbol({ symbol: "S", provider_symbol: "   ", exchange_symbol: "X" })).toBe("X");
    expect(displaySymbol({ symbol: "S", provider_symbol: "", exchange_symbol: "" })).toBe("S");
    expect(displaySymbol({ symbol: "S", provider_symbol: null, exchange_symbol: "E" })).toBe("E");
  });
});
describe("freshnessOf", () => {
  const fresh = () => (/* @__PURE__ */ new Date()).toISOString();
  it("maps fallback/delay to live|delayed|stale|cached", () => {
    expect(freshnessOf(prov({ as_of: fresh(), fallback_used: true, delay_minutes: 0 }))).toBe("cached");
    expect(freshnessOf(prov({ as_of: fresh(), delay_minutes: -1 }))).toBe("stale");
    expect(freshnessOf(prov({ as_of: fresh(), delay_minutes: 0 }))).toBe("live");
    expect(freshnessOf(prov({ as_of: fresh(), delay_minutes: 1 }))).toBe("live");
    expect(freshnessOf(prov({ as_of: fresh(), delay_minutes: 2 }))).toBe("delayed");
    expect(freshnessOf(prov({ as_of: fresh(), delay_minutes: 30 }))).toBe("delayed");
    expect(freshnessOf(prov({ as_of: fresh(), delay_minutes: 31 }))).toBe("stale");
    expect(freshnessOf(prov({ as_of: fresh(), delay_minutes: 120 }))).toBe("stale");
  });
  it("reads stale for old as_of even when the expected delay is small", () => {
    expect(freshnessOf(prov({ delay_minutes: 15 }))).toBe("stale");
    expect(freshnessOf(prov({ delay_minutes: 0 }))).toBe("stale");
  });
});
describe("constants and client defaults", () => {
  it("FORECAST_HORIZONS is [1, 7, 14, 21]", () => {
    expect(FORECAST_HORIZONS).toEqual([1, 7, 14, 21]);
    expect(FORECAST_HORIZONS).toHaveLength(4);
    expect(FORECAST_HORIZONS).toContain(21);
  });
  it("MARKET_STATES covers the five badge states", () => {
    expect(MARKET_STATES).toEqual(["open", "closed", "lunch", "delayed", "stale"]);
    expect(MARKET_STATES).toHaveLength(5);
  });
  it("timeout consts: AI/SCREENER 60s, shared axios default 60s", () => {
    expect(AI_TIMEOUT_MS).toBe(6e4);
    expect(SCREENER_TIMEOUT_MS).toBe(6e4);
    expect(api.defaults.timeout).toBe(6e4);
  });
  it("Node default baseURL targets local FastAPI (resolveBaseUrl contract)", () => {
    expect(api.defaults.baseURL).toBe("http://localhost:8000");
  });
});
describe("normalizeTargetCcy", () => {
  it("uppercases/trims offered currencies, defaults to USD", () => {
    expect(normalizeTargetCcy("usd")).toBe("USD");
    expect(normalizeTargetCcy(" eur ")).toBe("EUR");
    expect(normalizeTargetCcy("CNY")).toBe("CNY");
    expect(normalizeTargetCcy("GBP")).toBe("USD");
    expect(normalizeTargetCcy("")).toBe("USD");
    expect(normalizeTargetCcy(null)).toBe("USD");
  });
});
describe("isFreshFxProvenance (fresh-FX gate)", () => {
  it("accepts non-fallback A/B grades with delay 0..30", () => {
    expect(isFreshFxProvenance(prov({ delay_minutes: 5, quality_grade: "A" }))).toBe(true);
    expect(isFreshFxProvenance(prov({ delay_minutes: 0, quality_grade: "B" }))).toBe(true);
    expect(isFreshFxProvenance(prov({ delay_minutes: 30, quality_grade: "b" }))).toBe(true);
  });
  it("gates everything else", () => {
    expect(isFreshFxProvenance(prov({ fallback_used: true, delay_minutes: 0 }))).toBe(false);
    expect(isFreshFxProvenance(prov({ delay_minutes: -1 }))).toBe(false);
    expect(isFreshFxProvenance(prov({ delay_minutes: 31 }))).toBe(false);
    expect(isFreshFxProvenance(prov({ delay_minutes: 5, quality_grade: "C" }))).toBe(false);
    expect(isFreshFxProvenance(prov({ delay_minutes: 5, quality_grade: "U" }))).toBe(false);
    expect(isFreshFxProvenance(null)).toBe(false);
    expect(isFreshFxProvenance(void 0)).toBe(false);
  });
});
describe("isFxProvenanceMissingError", () => {
  it("detects the gate code across axios/fetch shapes (case-insensitive)", () => {
    expect(
      isFxProvenanceMissingError({ response: { data: { error: { code: "FX_PROVENANCE_MISSING" } } } })
    ).toBe(true);
    expect(isFxProvenanceMissingError({ response: { data: { code: "FX_PROVENANCE_MISSING" } } })).toBe(
      true
    );
    expect(isFxProvenanceMissingError({ code: "FX_PROVENANCE_MISSING" })).toBe(true);
    expect(isFxProvenanceMissingError({ code: "fx_provenance_missing" })).toBe(true);
    expect(isFxProvenanceMissingError({ message: "Cross-market ... FX_PROVENANCE_MISSING" })).toBe(
      true
    );
    expect(
      isFxProvenanceMissingError({ response: { data: { detail: "fx_provenance_missing" } } })
    ).toBe(true);
  });
  it("returns false for unrelated errors", () => {
    expect(isFxProvenanceMissingError({ message: "boom" })).toBe(false);
    expect(isFxProvenanceMissingError({})).toBe(false);
    expect(isFxProvenanceMissingError(null)).toBe(false);
  });
});
describe("friendlyAIError (timeout-aware)", () => {
  it("explains cold-start timeouts, passes through other failures", () => {
    expect(friendlyAIError({ code: "ECONNABORTED", message: "x" })).toContain("60s");
    expect(friendlyAIError(new Error("timeout of 15000ms exceeded"))).toContain("60s");
    expect(friendlyAIError(new Error("boom"))).toBe("AI request failed (boom).");
    expect(friendlyAIError("oops")).toContain("AI request failed");
  });
});
describe("normalizeHealthProviders (tracker rows lack name/status)", () => {
  it("maps backend tracker rows to the HealthSchema shape", () => {
    const out = normalizeHealthProviders([
      { provider: "yfinance", latency_p50_ms: 250, circuit: "closed" },
      { provider: "akshare", latency_p50_ms: 0, circuit: "open" }
    ]);
    expect(out[0]?.name).toBe("yfinance");
    expect(out[0]?.status).toBe("ok");
    expect(out[0]?.latency_ms).toBe(250);
    expect(out[1]?.status).toBe("open");
  });
  it("never fabricates ok-with-0ms: unknown state stays unknown, null latency dropped", () => {
    const out = normalizeHealthProviders([
      { provider: "akshare", state: "unknown", latency_p50_ms: null, latency_p95_ms: null, total_calls: 0, circuit: "closed" },
      { provider: "xai", state: "unconfigured", latency_p50_ms: null, circuit: "closed" }
    ]);
    expect(out[0]?.status).toBe("unknown");
    expect(out[0]?.latency_ms).toBe(void 0);
    expect(out[1]?.status).toBe("unconfigured");
  });
  it("keeps explicit name/status and passes non-arrays through as empty", () => {
    const out = normalizeHealthProviders([{ name: "x", status: "degraded" }]);
    expect(out[0]?.status).toBe("degraded");
    expect(normalizeHealthProviders(null)).toEqual([]);
    expect(normalizeHealthProviders({})).toEqual([]);
  });
});
describe("slow-path timeouts (cold serverless budget)", () => {
  it("backtest and rank get 60s like AI and screener", () => {
    expect(BACKTEST_TIMEOUT_MS).toBe(6e4);
    expect(RANK_TIMEOUT_MS).toBe(6e4);
  });
});
