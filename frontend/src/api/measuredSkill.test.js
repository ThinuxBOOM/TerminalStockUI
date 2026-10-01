import { describe, expect, it } from "vitest";
import { normalizeMeasuredSkill, normalizeMeasuredSkillTable, skillForHorizon } from "./measuredSkill";

const entry = (skill, ci, calibration = "shrinkage") => ({
  horizon_days: 7, calibration, skill, ci95: ci, verdict: "worse", symbols: 100, points: 31450, as_of: "2026-10-01", summary: "s",
});

describe("normalizeMeasuredSkill", () => {
  it("keeps a well-formed entry", () => {
    const s = normalizeMeasuredSkill(entry(-0.031, [-0.038, -0.024]));
    expect(s.skill).toBe(-0.031);
    expect(s.ci95).toEqual([-0.038, -0.024]);
    expect(s.verdict).toBe("worse");
  });
  it("rejects entries without a finite skill and drops a malformed interval", () => {
    expect(normalizeMeasuredSkill(null)).toBeNull();
    expect(normalizeMeasuredSkill({ skill: "bad" })).toBeNull();
    const s = normalizeMeasuredSkill({ skill: 0.01, ci95: [0.0], verdict: "nonsense" });
    expect(s.ci95).toBeNull();
    expect(s.verdict).toBe("unknown");
  });
  it("is idempotent, so components accept raw or normalized input", () => {
    const once = normalizeMeasuredSkill(entry(-0.02, [-0.03, -0.01]));
    expect(normalizeMeasuredSkill(once)).toEqual(once);
  });
});

describe("skillForHorizon", () => {
  const table = normalizeMeasuredSkillTable({
    measured: true,
    horizons: { 7: { shrinkage: entry(-0.031, [-0.04, -0.02]), isotonic: entry(-0.08, [-0.1, -0.06], "isotonic") } },
  });
  it("shows the less flattering calibration when the page cannot know which applies", () => {
    expect(skillForHorizon(table, 7).skill).toBe(-0.08);
  });
  it("uses the named calibration when the caller knows it", () => {
    expect(skillForHorizon(table, 7, "shrinkage").skill).toBe(-0.031);
  });
  it("returns null for an unmeasured horizon or a missing table", () => {
    expect(skillForHorizon(table, 14)).toBeNull();
    expect(skillForHorizon(undefined, 7)).toBeNull();
  });
});
