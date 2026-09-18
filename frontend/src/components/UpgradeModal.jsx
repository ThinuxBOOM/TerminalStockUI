// Global 402 upsell modal. The axios response interceptor (client.js)
// dispatches `onemarket:upgrade-required` with the server body as detail
// ({upgrade_required, min_tier, ...}); this modal listens and offers /pricing.
// Pure helper `upgradeDetailFor` is exported for node tests.

import React, { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { displayTier } from "./RequireTier";

const UPGRADE_EVENT = "onemarket:upgrade-required";

// Pure + node-testable: normalize a 402 payload into modal copy.
function upgradeDetailFor(detail) {
  const d = detail && typeof detail === "object" ? detail : {};
  const minTier = String(d.min_tier ?? d.minTier ?? "silver");
  const feature = String(d.feature ?? d.detail ?? "This feature");
  return { minTier: displayTier(minTier), feature };
}

function UpgradeModal() {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState(null);

  useEffect(() => {
    if (typeof window === "undefined") return undefined;
    const onUpgrade = (e) => {
      try {
        setDetail(e?.detail ?? null);
      } catch {
        setDetail(null);
      }
      setOpen(true);
    };
    window.addEventListener(UPGRADE_EVENT, onUpgrade);
    return () => window.removeEventListener(UPGRADE_EVENT, onUpgrade);
  }, []);

  const close = useCallback(() => {
    setOpen(false);
    setDetail(null);
  }, []);

  useEffect(() => {
    if (!open || typeof window === "undefined") return undefined;
    const onKey = (e) => {
      if (e.key === "Escape") close();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, close]);

  if (!open) return null;
  const { minTier, feature } = upgradeDetailFor(detail);
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      role="dialog"
      aria-modal="true"
      aria-label="Upgrade required"
      onClick={close}
    >
      <div
        className="term-panel w-full max-w-md p-5"
        role="document"
        onClick={(e) => e.stopPropagation()}
      >
        <p className="term-label">🔒 UPGRADE REQUIRED · {minTier.toUpperCase()}+</p>
        <h2 className="mt-1 text-lg font-extrabold text-term-text">
          {feature} needs {minTier}
        </h2>
        <p className="mt-1 text-sm text-term-muted">
          Your plan doesn&apos;t include this yet. Upgrade on Stripe — access
          unlocks as soon as payment completes.
        </p>
        <div className="mt-4 flex flex-wrap gap-2">
          <Link to="/pricing" className="term-btn text-xs" onClick={close}>
            SEE PLANS →
          </Link>
          <button type="button" className="term-btn-ghost text-xs" onClick={close}>
            NOT NOW
          </button>
        </div>
      </div>
    </div>
  );
}

export { UPGRADE_EVENT, UpgradeModal as default, upgradeDetailFor };
