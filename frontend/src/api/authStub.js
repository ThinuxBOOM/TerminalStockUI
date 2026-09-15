import { PLAN_TIERS, TIER_FEATURES } from "./client";

// Future-prep auth/tier stub (NO real auth, NO billing, NO gating).
//
// Mirrors backend/auth/tiers.py: Free/Silver/Gold/Platinum + guest user.
// Everything resolves to guest/free today; the stub tier selector persists to
// localStorage only so future per-user caching keys (aspi:<mic>:<tf>:u:<id>:t:<tier>)
// already work without migration. Callers must NEVER gate on `locked` — UI
// always renders unlocked paths with `locked=false`.

const TIER_ORDER = [...PLAN_TIERS];
const STORAGE_TIER_KEY = "auth:stub:tier";
const STORAGE_USER_KEY = "auth:stub:userId";

function normalizeTier(v) {
  const s = String(v ?? "Free").trim();
  const hit = TIER_ORDER.find((t) => t.toLowerCase() === s.toLowerCase());
  return hit ?? "Free";
}

function normId(v, fallback) {
  const s = String(v ?? "").trim();
  return s !== "" ? s : fallback;
}

function loadStubTier() {
  try {
    if (typeof localStorage === "undefined") return "Free";
    return normalizeTier(localStorage.getItem(STORAGE_TIER_KEY) ?? "Free");
  } catch {
    return "Free";
  }
}

function saveStubTier(tier) {
  try {
    if (typeof localStorage === "undefined") return false;
    localStorage.setItem(STORAGE_TIER_KEY, normalizeTier(tier));
    return true;
  } catch {
    return false;
  }
}

function loadStubUserId() {
  try {
    if (typeof localStorage === "undefined") return null;
    const raw = localStorage.getItem(STORAGE_USER_KEY);
    const s = typeof raw === "string" ? raw.trim() : "";
    return s !== "" ? s : null;
  } catch {
    return null;
  }
}

function saveStubUserId(userId) {
  try {
    if (typeof localStorage === "undefined") return false;
    if (userId === null || userId === undefined || String(userId).trim() === "") {
      localStorage.removeItem(STORAGE_USER_KEY);
    } else {
      localStorage.setItem(STORAGE_USER_KEY, String(userId).trim());
    }
    return true;
  } catch {
    return false;
  }
}

// Guest stub: { userId: null, tier: "Free"|..., isGuest: true }. Reads the
// persisted stub tier for forward-compat testing (still guest: userId stays
// null unless a future auth flow sets it). Never throws, never gates.
function getCurrentUserStub() {
  return { userId: loadStubUserId(), tier: loadStubTier(), isGuest: true };
}

function tierRank(tier) {
  return Math.max(0, TIER_ORDER.indexOf(normalizeTier(tier)));
}

// Pure tier->feature check for FUTURE gating. UI must not branch on it yet
// (always render unlocked); exposed so the mapping is testable in one place.
function canUseFeatureStub(tier, feature) {
  const meta = TIER_FEATURES[feature] ?? { minTier: "Free" };
  return tierRank(tier) >= tierRank(meta.minTier);
}

function quotaNoteFor(tier, feature) {
  const t = normalizeTier(tier);
  const table = {
    Free: "guest quotas (reference only — not enforced)",
    Silver: "1k quick-insight/mo reference (not enforced)",
    Gold: "3k quick-insight/mo reference (not enforced)",
    Platinum: "8k quick-insight/mo reference (not enforced)",
  };
  return `${feature} · ${t} tier · ${table[t] ?? "reference only"}`;
}

export {
  STORAGE_TIER_KEY,
  STORAGE_USER_KEY,
  TIER_ORDER,
  canUseFeatureStub,
  getCurrentUserStub,
  loadStubTier,
  loadStubUserId,
  normalizeTier,
  normId,
  quotaNoteFor,
  saveStubTier,
  saveStubUserId,
};
