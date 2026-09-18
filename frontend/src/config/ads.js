// V2 compliant-ads config (visible slots only).
//
// Premium = DON'T MOUNT / DON'T REQUEST — never render-then-hide. Hidden or
// invisible impressions (opacity:0, display:none, 1x1, off-screen, stacked,
// background layers) are invalid traffic per AdSense policy and are OUT.
// Suppressed tiers therefore return null from <AdSlot/> (zero ad requests),
// which is what AdSlot.test.js asserts at the logic level.
//
// Page budget (free tier, the max): Layout leaderboard (index 0) + Layout
// footer in-feed (index 1) + at most ONE page-level slot (index 2) = 3.
// Silver drops the page slot (2), gold keeps the leaderboard only (1),
// platinum mounts nothing (0).

// Per-tier visible-slot budget. Counts are enforced by NOT mounting slots
// whose slotIndex >= budget — never by hiding mounted nodes.
const AD_INTENSITY = {
  free: 3,
  silver: 2,
  gold: 1,
  platinum: 0,
};

const AD_TIER_ORDER = ["free", "silver", "gold", "platinum"];

// IntersectionObserver rootMargin: start loading ~200px before the slot
// enters the viewport (per V2 plan). Exported so AdSlot.jsx and its test
// share one source of truth.
const AD_IO_ROOT_MARGIN = "200px";

// CLS reserve per slot format (min-height, px). The labeled container always
// occupies this space, so lazy ad fill never shifts layout.
const AD_SLOT_MIN_HEIGHTS = {
  leaderboard: 90,
  "in-feed": 250,
  "in-article": 250,
  auto: 120,
};

const AD_FORMATS = Object.keys(AD_SLOT_MIN_HEIGHTS);

// Placeholder publisher ID shipped until AdSense onboarding completes
// (zero-traffic starter). Pushes are skipped while this is configured.
const ADS_CLIENT_PLACEHOLDER = "ca-pub-XXXXXXXXXXXXXXXX";

function normalizeAdTier(v) {
  const s = String(v ?? "free").trim().toLowerCase();
  return AD_TIER_ORDER.includes(s) ? s : "free";
}

// Pure parser (node-testable): only the string "false" (case-insensitive,
// trimmed) disables ads. Unset/empty/any-other-value => enabled.
function parseAdsEnabled(v) {
  if (v === undefined || v === null) return true;
  return String(v).trim().toLowerCase() !== "false";
}

function readEnv(key) {
  try {
    if (typeof import.meta !== "undefined" && import.meta.env && key in import.meta.env) {
      return import.meta.env[key];
    }
  } catch {
    // non-Vite runtimes (node tests) fall through
  }
  try {
    if (typeof process !== "undefined" && process.env && key in process.env) {
      return process.env[key];
    }
  } catch {
    // ignore
  }
  return undefined;
}

// Kill-switch: VITE_ADS_ENABLED=false collapses all slots (mounts null).
function adsEnabled() {
  return parseAdsEnabled(readEnv("VITE_ADS_ENABLED"));
}

function adsClientId() {
  const raw = readEnv("VITE_ADS_CLIENT_ID");
  const s = typeof raw === "string" ? raw.trim() : "";
  return s;
}

// A client ID is pushable only when it looks like a real AdSense publisher
// ID — the placeholder (or empty) means "reserve + label, zero requests".
function isPushableClientId(clientId) {
  const s = String(clientId ?? "").trim();
  if (s === "" || s === ADS_CLIENT_PLACEHOLDER) return false;
  return /^ca-pub-\d+$/.test(s);
}

// dev => data-adtest="on" so test impressions never bill. Pure + explicit
// args so tests stay deterministic regardless of runner env.
function resolveAdTestMode({ dev = false, clientId = "" } = {}) {
  if (dev) return true;
  return !isPushableClientId(clientId);
}

function isDevEnv() {
  try {
    if (typeof import.meta !== "undefined" && import.meta.env && import.meta.env.DEV === true) return true;
  } catch {
    // ignore
  }
  try {
    if (typeof process !== "undefined" && process.env && process.env.NODE_ENV !== "production") return true;
  } catch {
    // ignore
  }
  return false;
}

function isAdTestMode() {
  return resolveAdTestMode({ dev: isDevEnv(), clientId: adsClientId() });
}

// Core suppression rule: mount slot `slotIndex` for `tier` only when the
// tier budget covers it. platinum (0) => every index false => zero requests.
function shouldMountSlot(tier, slotIndex = 0) {
  const t = normalizeAdTier(tier);
  const idx = Number.isFinite(Number(slotIndex)) ? Math.max(0, Math.floor(Number(slotIndex))) : 0;
  return idx < (AD_INTENSITY[t] ?? 0);
}

function slotMinHeight(format) {
  const f = String(format ?? "auto").trim();
  return AD_SLOT_MIN_HEIGHTS[f] ?? AD_SLOT_MIN_HEIGHTS.auto;
}

export {
  ADS_CLIENT_PLACEHOLDER,
  AD_FORMATS,
  AD_INTENSITY,
  AD_IO_ROOT_MARGIN,
  AD_SLOT_MIN_HEIGHTS,
  AD_TIER_ORDER,
  adsClientId,
  adsEnabled,
  isAdTestMode,
  isDevEnv,
  isPushableClientId,
  normalizeAdTier,
  parseAdsEnabled,
  readEnv,
  resolveAdTestMode,
  shouldMountSlot,
  slotMinHeight,
};
