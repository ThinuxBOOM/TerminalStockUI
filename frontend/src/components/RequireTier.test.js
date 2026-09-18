import { describe, expect, it } from "vitest";
import {
  displayTier,
  meetsTierRequirement,
  normalizeRankTier,
} from "./RequireTier";
import { upgradeDetailFor } from "./UpgradeModal";

describe("RequireTier mirror (UX only — backend enforces)", () => {
  it("free meets free, but not silver+", () => {
    expect(meetsTierRequirement("free", "free")).toBe(true);
    expect(meetsTierRequirement("Free", "Silver")).toBe(false);
    expect(meetsTierRequirement("free", "gold")).toBe(false);
    expect(meetsTierRequirement("free", "platinum")).toBe(false);
  });

  it("higher tiers meet lower minimums", () => {
    expect(meetsTierRequirement("Silver", "Silver")).toBe(true);
    expect(meetsTierRequirement("Gold", "Silver")).toBe(true);
    expect(meetsTierRequirement("Platinum", "Gold")).toBe(true);
    expect(meetsTierRequirement("Gold", "Platinum")).toBe(false);
  });

  it("admin bypasses every minimum (mirror of backend is_admin allow-all)", () => {
    expect(meetsTierRequirement("free", "platinum", { isAdmin: true })).toBe(true);
    expect(meetsTierRequirement("free", "platinum", { isAdmin: false })).toBe(false);
  });

  it("tier names are case-insensitive, unknown tiers fail closed to free", () => {
    expect(normalizeRankTier("GOLD")).toBe("gold");
    expect(normalizeRankTier("bogus")).toBe("free");
    expect(displayTier("gold")).toBe("Gold");
    expect(meetsTierRequirement("bogus", "silver")).toBe(false);
  });
});

describe("402 upsell detail", () => {
  it("normalizes server payloads into modal copy", () => {
    expect(upgradeDetailFor({ min_tier: "gold", feature: "Backtest" })).toEqual({
      minTier: "Gold",
      feature: "Backtest",
    });
    expect(upgradeDetailFor(null).minTier).toBe("Silver");
    expect(upgradeDetailFor({}).feature).toBeTruthy();
  });
});
