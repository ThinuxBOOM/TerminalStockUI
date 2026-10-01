import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { DirectionLean, ForecastOutlook, MeasuredSkillNote } from "./ForecastOutlook";

const text = (el) => renderToStaticMarkup(el).replace(/<[^>]+>/g, "");

const forecast = {
  horizon_days: 7,
  probability: 0.542,
  validation_status: "experimental",
  intervals: { low: -0.041, mid: 0.004, high: 0.05 },
  regime: "normal",
  drawdown: 0.03,
  drawdown_detail: { threshold: 0.1 },
  measured_skill: { skill: -0.0311, ci95: [-0.0382, -0.0239], verdict: "worse", symbols: 100, as_of: "2026-10-01" },
};

describe("ForecastOutlook", () => {
  it("leads with the return range and drop risk, not the probability", () => {
    const t = text(<ForecastOutlook forecast={forecast} currency="USD" />);
    expect(t).toContain("7-day range");
    expect(t).toContain("−4.1% to +5.0%");
    expect(t).toContain("Chance of a 10%+ drop within 7d: 3%");
    expect(t).not.toContain("54.2%");
  });
  it("says unavailable instead of inventing a range", () => {
    expect(text(<ForecastOutlook forecast={{ ...forecast, intervals: null }} />)).toContain("range unavailable");
  });
});

describe("DirectionLean", () => {
  it("shows the probability with its measured record", () => {
    const t = text(<DirectionLean forecast={forecast} />);
    expect(t).toContain("54.2%");
    expect(t).toContain("EXPERIMENTAL");
    expect(t).toContain("Measured: worse than the base rate");
    expect(t).toContain("−0.031 (95% CI −0.038 to −0.024)");
  });
});

describe("MeasuredSkillNote", () => {
  it("is explicit when a model version has not been measured", () => {
    expect(text(<MeasuredSkillNote skill={null} />)).toContain("not measured for this model version");
  });
  it("describes an interval spanning zero as no better", () => {
    const t = text(<MeasuredSkillNote skill={{ skill: -0.011, ci95: [-0.03, 0.007], verdict: "indistinguishable" }} compact />);
    expect(t).toContain("no better than the base rate");
    expect(t).not.toContain("walk-forward on");
  });
});
