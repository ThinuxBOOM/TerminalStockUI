import React from "react";
import { Link, useParams } from "react-router-dom";
import SecurityBrief from "../features/security/SecurityBrief";
import AdSlot from "../components/AdSlot";
import { useAuth } from "../hooks/useAuth";

function safeDecode(v) {
  try {
    return decodeURIComponent(v);
  } catch {
    return v;
  }
}

function SecurityBriefPage() {
  const { symbol = "AAPL" } = useParams();
  const decoded = safeDecode(symbol);
  const enc = encodeURIComponent(decoded);
  // Tier mirror for the single in-article ad below (guest-safe, zero requests).
  const { tier } = useAuth();
  // Indicator-favorites onboarding lives on /welcome (per-user favorites stub
  // `indicators:<userId||guest>`). Auth is NOT implemented — this link must
  // not gate any chart behavior.
  const welcomeHref = `/welcome?from=security/${enc}`;
  return (
    <div className="max-w-full">
      <nav className="mb-3 flex flex-wrap items-center gap-2 text-xs" aria-label="Breadcrumb">
        <Link to="/" className="text-term-muted hover:text-term-text focus-visible:text-term-text">← Home</Link>
        <span className="text-term-muted" aria-hidden="true">/</span>
        <Link to={`/forecast/${enc}#research`} className="text-term-muted hover:text-term-text">Research (A/B)</Link>
        <span className="text-term-muted" aria-hidden="true">/</span>
        <Link to={`/forecast/${enc}`} className="text-term-muted hover:text-term-text">Forecast</Link>
        <span className="text-term-muted" aria-hidden="true">/</span>
        <Link to={`/backtest?symbol=${enc}`} className="text-term-muted hover:text-term-text">Backtest</Link>
        <span className="text-term-muted" aria-hidden="true">/</span>
        <Link to={welcomeHref} className="text-term-muted hover:text-term-text" title="Placeholder: indicator-favorites onboarding on the future welcome page (no auth yet)">Welcome</Link>
      </nav>
      <SecurityBrief symbol={decoded} />
      {/* V2 compliant ads: one in-article slot below the brief (slot index 2
          of the per-tier budget). Never inside table rows. */}
      <AdSlot slotId="brief-inarticle" format="in-article" tier={tier} slotIndex={2} />
    </div>
  );
}

export { SecurityBriefPage as default };
