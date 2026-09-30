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
  return (
    <div className="max-w-full">
      <nav className="mb-3 flex flex-wrap items-center gap-2 text-xs" aria-label="Breadcrumb">
        <Link to="/app" className="text-term-muted hover:text-term-text focus-visible:text-term-text">← Home</Link>
        <span className="text-term-muted" aria-hidden="true">/</span>
        <Link to={`/forecast/${enc}#research`} className="text-term-muted hover:text-term-text">Research (A/B)</Link>
        <span className="text-term-muted" aria-hidden="true">/</span>
        <Link to={`/forecast/${enc}`} className="text-term-muted hover:text-term-text">Forecast</Link>
        <span className="text-term-muted" aria-hidden="true">/</span>
        <Link to={`/backtest?symbol=${enc}`} className="text-term-muted hover:text-term-text">Backtest</Link>
      </nav>
      <SecurityBrief symbol={decoded} />
    </div>
  );
}

export { SecurityBriefPage as default };
