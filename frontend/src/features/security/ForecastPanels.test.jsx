import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { ForecastSummaryCard, ForecastTab } from "./ForecastPanels";

const text = (el) => renderToStaticMarkup(<MemoryRouter>{el}</MemoryRouter>).replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");

function forecast(over = {}) {
  return {
    horizon_days: 21,
    expected_return_range: { low: -0.099, mid: 0.014, high: 0.124 },
    quantiles: { "0.05": -0.14, "0.10": -0.099, "0.25": -0.04, "0.50": 0.014, "0.75": 0.06, "0.90": 0.124, "0.95": 0.16 },
    target_price: { last_close: 230, low: 207.98, mid: 233, high: 259.57 },
    drawdown_probability: 0.16,
    volatility_forecast_annual: 0.33,
    volatility_regime: "low",
    direction_probability: 0.567,
    base_rate: 0.568,
    outperform_probability: 0.498,
    outperform_rank: 0.45,
    relative_available: true,
    signal_strength: "weak",
    drivers: { for: [{ feature: "dist_sma200", label: "distance from the 200-day average", percentile: 0.85 }], against: [] },
    measured: { range_coverage_80: 0.793, drop_risk_skill: 0.05, out_ic: 0.025, out_ic_t: 1.25, out_decile_spread: 0.0076, up_skill: -0.001 },
    limitations: ["Daily data."],
    summary: "Over the next 21 trading days...",
    model_version: "v4-test",
    data_version: "d",
    disclosure: "Not investment advice.",
    ...over,
  };
}

describe("ForecastSummaryCard", () => {
  it("leads with the range, then rank and drop risk", () => {
    const t = text(<ForecastSummaryCard forecasts={{ 21: forecast() }} horizon={21} currency="USD" />);
    expect(t.indexOf("Likely 21-day range")).toBeLessThan(t.indexOf("Outperformance rank"));
    expect(t).toContain("−9.9% to +12.4%");
    expect(t).toContain("Bottom 45%");
    expect(t).toContain("16%");
  });
  it("says the ranking covers US listings only when unavailable", () => {
    const t = text(<ForecastSummaryCard forecasts={{ 21: forecast({ relative_available: false, outperform_rank: null }) }} horizon={21} />);
    expect(t).toContain("US listings only");
  });
});

describe("ForecastTab", () => {
  it("shows the measured record next to the numbers", () => {
    const t = text(<ForecastTab forecasts={{ 21: forecast() }} horizon={21} currency="USD" />);
    expect(t).toContain("How accurate is this?");
    expect(t).toContain("79.3%");
    expect(t).toContain("base rate 56.8%");
    expect(t).toContain("Distance from the 200-day average");
  });
  it("renders an error instead of numbers when the forecast failed", () => {
    expect(text(<ForecastTab error="NVDA needs 253 daily bars" />)).toContain("needs 253 daily bars");
  });
});
