import { describe, expect, it } from "vitest";
import {
  canUseFeatureStub,
  getCurrentUserStub,
  normalizeTier,
  quotaNoteFor,
} from "./authStub";

describe("auth stub (future-prep, never gates)", () => {
  it("resolves to guest/free without throwing (node-safe, no localStorage)", () => {
    const u = getCurrentUserStub();
    expect(u.isGuest).toBe(true);
    expect(u.userId).toBeNull();
    expect(["Free", "Silver", "Gold", "Platinum"]).toContain(u.tier);
  });

  it("normalizeTier falls back to Free on unknown input", () => {
    expect(normalizeTier("gold")).toBe("Gold");
    expect(normalizeTier("bogus")).toBe("Free");
    expect(normalizeTier(undefined)).toBe("Free");
  });

  it("tier mapping is pure and testable (UI must not gate on it yet)", () => {
    expect(canUseFeatureStub("Free", "Quick Insight")).toBe(true);
    expect(canUseFeatureStub("Free", "Deep Research")).toBe(false);
    expect(canUseFeatureStub("Silver", "Deep Research")).toBe(true);
  });

  it("quota notes are reference-only copy", () => {
    expect(quotaNoteFor("Free", "Deep Research")).toMatch(/not enforced/i);
  });
});
