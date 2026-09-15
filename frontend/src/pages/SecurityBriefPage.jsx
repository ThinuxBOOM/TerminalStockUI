import React from "react";
import { Link, useParams } from "react-router-dom";
import SecurityBrief from "../features/security/SecurityBrief";

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
  // Indicator-favorites onboarding lives on /welcome (per-user favorites stub
  // `indicators:<userId||guest>`). Auth is NOT implemented — this link must
  // not gate any chart behavior.
  const welcomeHref = `/welcome?from=security/${enc}`;
  return (
    <div>
      <nav className="mb-3 flex flex-wrap items-center gap-2 text-xs" aria-label="Breadcrumb">
        <Link to="/" className="text-term-muted hover:text-term-text">← Home</Link>
        <span className="text-term-muted" aria-hidden="true">/</span>
        <Link to={`/forecast/${enc}#research`} className="text-term-muted hover:text-term-text">Research (A/B)</Link>
        <span className="text-term-muted" aria-hidden="true">/</span>
        <Link to={`/forecast/${enc}`} className="text-term-muted hover:text-term-text">Forecast</Link>
        <span className="text-term-muted" aria-hidden="true">/</span>
        <Link to={`/backtest?symbol=${enc}`} className="text-term-muted hover:text-term-text">Backtest</Link>
        <span className="text-term-muted" aria-hidden="true">/</span>
        <Link to={welcomeHref} className="text-term-muted hover:text-term-text" title="Placeholder: indicator-favorites onboarding on the future welcome page (no auth yet)">Welcome</Link>
      </nav>
      <h1 className="mb-3 text-sm tracking-widest text-term-muted">SECURITY BRIEF · {decoded}</h1>
      <SecurityBrief symbol={decoded} />
    </div>
  );
}

export { SecurityBriefPage as default };
