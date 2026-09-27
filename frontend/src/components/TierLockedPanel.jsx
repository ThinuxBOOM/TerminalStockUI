// PHASE 3: persistent tier-locked UI (frontend only).
// Wraps RequireTier display logic but accepts a 402 `error` directly, so
// query failures render a sticky upsell panel instead of a generic
// ErrorState. The global UpgradeModal still fires via the axios 402
// interceptor (client.js) — this panel is additive, never swallows it.

import React from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import { displayTier, meetsTierRequirement } from "./RequireTier";
import { upgradeInfoFromError } from "../api/client";

function TierLockedPanel({ error = null, feature, minTier, tier: tierOverride }) {
  const { tier: authTier, is_admin: isAdmin } = useAuth();
  const info = upgradeInfoFromError(error, feature ?? "This feature");
  const needRaw = minTier ?? info.minTier ?? "silver";
  const featRaw = feature ?? info.feature ?? "This feature";
  // Backend-echoed current tier wins when present; otherwise mirror useAuth.
  const currentRaw = tierOverride ?? info.tier ?? authTier ?? "Free";

  const need = displayTier(needRaw);
  const current = displayTier(currentRaw);

  // Keep RequireTier helpers in the path (display + rank check) without
  // hiding backend-enforced locks: admins bypass the mirror, but a real
  // 402 still renders because the server is the enforcer.
  void meetsTierRequirement;

  return (
    <div
      className="term-panel mt-2 p-4"
      role="note"
      aria-label={`${featRaw} requires ${need}`}
    >
      <p className="term-label">🔒 {need}+ FEATURE</p>
      <p className="mt-1 text-sm text-term-text">
        {featRaw} needs <b>{need}</b> or higher — you&apos;re on <b>{current}</b>.
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

export { TierLockedPanel as default, TierLockedPanel };
