import { describe, expect, it } from "vitest";
import { formatMetric } from "./format";

describe("formatMetric", () => {
  it("shows the value, with the formula as a tooltip", () => {
    const m = formatMetric({ value: 0.123456, formula: "net_income / revenue", source_fields: ["net_income", "revenue"], quality_flag: "ok", reason: null });
    expect(m.text).toBe("0.1235");
    expect(m.title).toContain("net_income / revenue");
    expect(m.unavailable).toBe(false);
  });
  it("explains unavailable metrics instead of dumping JSON", () => {
    const m = formatMetric({ value: null, formula: "f", source_fields: [], quality_flag: "unavailable", reason: "statements missing" });
    expect(m.text).toBe("unavailable (statements missing)");
    expect(m.unavailable).toBe(true);
  });
  it("summarizes series and frames by their latest values", () => {
    expect(formatMetric({ value: { kind: "series", latest: 61.2 }, formula: "", source_fields: [], quality_flag: "ok" }).text).toBe("61.2");
    expect(formatMetric({ value: { kind: "frame", latest: { macd: 1.5, signal: 1.25 } }, formula: "", source_fields: [], quality_flag: "ok" }).text).toBe("macd 1.5 · signal 1.25");
    expect(formatMetric("plain").text).toBe("plain");
  });
});
