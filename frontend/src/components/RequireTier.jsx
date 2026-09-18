// UX mirror for tier gating ONLY — the backend (Depends(require_tier))
// is the enforcer. This wrapper never blocks data fetching; it only swaps
// locked UI for an upsell panel linking /pricing. 402 responses surface the
// same upsell via <UpgradeModal/> (driven by the axios 402 handler).

import React from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";

// Lowercase rank map (matches backend _TIER_RANK order). Case-insensitive,
// unknown tiers fall back to free (fail-closed for UX: show upsell).
const TIER_RANK = {
  free: 0,
  silver: 1,
  gold: 2,
  platinum: 3,
};

function normalizeRankTier(v) {
  const s = String(v ?? "free").trim().toLowerCase();
  return s in TIER_RANK ? s : "free";
}

// Pure mirror check (node-testable): does `tier` meet `minTier`?
// Admins bypass (mirror of backend require_admin / is_admin allow-all).
function meetsTierRequirement(tier, minTier, { isAdmin = false } = {}) {
  if (isAdmin === true) return true;
  const have = TIER_RANK[normalizeRankTier(tier)] ?? 0;
  const need = TIER_RANK[normalizeRankTier(minTier ?? "free")] ?? 0;
  return have >= need;
}

function displayTier(v) {
  const s = normalizeRankTier(v);
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function RequireTier({ min = "silver", feature = "this feature", children, fallback = null }) {
  const { tier, is_admin: isAdmin } = useAuth();
  if (meetsTierRequirement(tier, min, { isAdmin })) {
    return <>{children}</>;
  }
  if (fallback !== null && fallback !== undefined) return <>{fallback}</>;
  return (
    <div
      className="term-panel mt-2 p-4"
      role="note"
      aria-label={`${feature} requires ${displayTier(min)}`}
    >
      <p className="term-label">🔒 {displayTier(min)}+ FEATURE</p>
      <p className="mt-1 text-sm text-term-text">
        {feature} needs <b>{displayTier(min)}</b> or higher — you&apos;re on{" "}
        <b>{displayTier(tier)}</b>.
      </p>
      <p className="mt-1 text-xs text-term-muted">
        Limits are enforced by the server; upgrading unlocks this panel instantly.
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        <Link to="/pricing" className="term-btn text-xs">
          SEE PLANS →
        </Link>
        <Link to="/account" className="term-btn-ghost text-xs">
          MY SUBSCRIPTION
        </Link>
      </div>
    </div>
  );
}

export { RequireTier as default, TIER_RANK, displayTier, meetsTierRequirement, normalizeRankTier };
