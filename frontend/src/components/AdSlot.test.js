import { describe, expect, it } from "vitest";
import {
  AD_INTENSITY,
  AD_IO_ROOT_MARGIN,
  AD_SLOT_MIN_HEIGHTS,
  isPushableClientId,
  parseAdsEnabled,
  resolveAdTestMode,
  shouldMountSlot,
  slotMinHeight,
} from "../config/ads";
// Component honors these exact constants (import proves the wiring; DOM
// mounting is out of scope — vitest here is node-env, pure-logic only).
import { AD_PUSH_TIMEOUT_MS } from "./AdSlot";

describe("AdSlot suppression (premium = don't mount, zero requests)", () => {
  it("platinum mounts zero slots at every index", () => {
    for (const idx of [0, 1, 2, 3, 10]) {
      expect(shouldMountSlot("platinum", idx)).toBe(false);
    }
    expect(AD_INTENSITY.platinum).toBe(0);
  });

  it("budgets shrink per tier: free 3 / silver 2 / gold 1 / platinum 0", () => {
    expect(AD_INTENSITY).toMatchObject({ free: 3, silver: 2, gold: 1, platinum: 0 });
    expect(shouldMountSlot("free", 2)).toBe(true);
    expect(shouldMountSlot("free", 3)).toBe(false);
    expect(shouldMountSlot("silver", 1)).toBe(true);
    expect(shouldMountSlot("silver", 2)).toBe(false);
    expect(shouldMountSlot("gold", 0)).toBe(true);
    expect(shouldMountSlot("gold", 1)).toBe(false);
  });

  it("tier matching is case-insensitive, unknown tiers fail closed to free budget", () => {
    expect(shouldMountSlot("Gold", 0)).toBe(true);
    expect(shouldMountSlot("SILVER", 2)).toBe(false);
    expect(shouldMountSlot("bogus", 2)).toBe(true); // free fallback
    expect(shouldMountSlot(undefined, 0)).toBe(true);
  });
});

describe("AdSlot CLS reserve present", () => {
  it("every format reserves a positive min-height", () => {
    for (const [format, h] of Object.entries(AD_SLOT_MIN_HEIGHTS)) {
      expect(h, format).toBeGreaterThan(0);
    }
    expect(slotMinHeight("leaderboard")).toBe(90);
    expect(slotMinHeight("in-feed")).toBe(250);
    expect(slotMinHeight("in-article")).toBe(250);
  });

  it("unknown format falls back to the auto reserve (never zero)", () => {
    expect(slotMinHeight("bogus")).toBe(AD_SLOT_MIN_HEIGHTS.auto);
    expect(slotMinHeight(undefined)).toBeGreaterThan(0);
  });

  it("IntersectionObserver rootMargin is ~200px (loads near viewport only)", () => {
    expect(AD_IO_ROOT_MARGIN).toBe("200px");
  });

  it("push watchdog is bounded (no retry loop — single push, then give up)", () => {
    expect(AD_PUSH_TIMEOUT_MS).toBeGreaterThan(0);
    expect(AD_PUSH_TIMEOUT_MS).toBeLessThanOrEqual(15000);
  });
});

describe("AdSlot adtest in dev / unconfigured publisher", () => {
  it("dev forces adtest mode (test impressions never bill)", () => {
    expect(resolveAdTestMode({ dev: true, clientId: "ca-pub-123" })).toBe(true);
  });

  it("placeholder or empty publisher ID is never pushable (zero requests)", () => {
    expect(isPushableClientId("")).toBe(false);
    expect(isPushableClientId("ca-pub-XXXXXXXXXXXXXXXX")).toBe(false);
    expect(isPushableClientId("ca-pub-1234567890123456")).toBe(true);
  });

  it("unpushable publisher forces adtest even outside dev", () => {
    expect(resolveAdTestMode({ dev: false, clientId: "" })).toBe(true);
    expect(resolveAdTestMode({ dev: false, clientId: "ca-pub-123" })).toBe(false);
  });
});

describe("VITE_ADS_ENABLED kill-switch", () => {
  it("only the string 'false' collapses slots", () => {
    expect(parseAdsEnabled("false")).toBe(false);
    expect(parseAdsEnabled("FALSE")).toBe(false);
    expect(parseAdsEnabled(" true ")).toBe(true);
    expect(parseAdsEnabled("")).toBe(true);
    expect(parseAdsEnabled(undefined)).toBe(true);
    expect(parseAdsEnabled(null)).toBe(true);
  });
});
