import React from "react";
import { Link, useParams } from "react-router-dom";
import ForecastDetails from "../features/forecast/ForecastDetails";
import AdSlot from "../components/AdSlot";
import { useAuth } from "../hooks/useAuth";

function safeDecode(v) {
  try {
    return decodeURIComponent(v);
  } catch {
    return v;
  }
}

// Phase 4 Research: scroll progression Quote -> Forecast -> Analytics ->
// Events -> Backtest mirrors the app hierarchy. Anchor nav is keyboard
// accessible and collapses to a single column on mobile.
function ForecastDetailsPage() {
  const { symbol = "AAPL" } = useParams();
  const decoded = safeDecode(symbol);
  const enc = encodeURIComponent(decoded);
  // Tier mirror for the single in-article ad below (guest-safe, zero requests).
  const { tier } = useAuth();
  return (
    <div className="min-w-0">
      <nav className="mb-3 flex flex-wrap items-center gap-2 text-xs" aria-label="Breadcrumb">
        <Link to="/app" className="text-term-muted hover:text-term-text">← Home</Link>
        <span className="text-term-muted" aria-hidden="true">/</span>
        <Link to={`/security/${enc}`} className="text-term-muted hover:text-term-text">Security Brief</Link>
        <span className="text-term-muted" aria-hidden="true">/</span>
        <Link to={`/backtest?symbol=${enc}`} className="text-term-muted hover:text-term-text">Backtest</Link>
      </nav>
      <h1 className="mb-3 text-sm tracking-widest text-term-muted">FORECAST DETAILS · {decoded}</h1>
      <nav
        className="mb-4 flex flex-wrap items-center gap-1.5 text-[11px]"
        aria-label="Research sections: quote, forecast, analytics, events, backtest"
      >
        <span className="mr-1 text-term-muted" aria-hidden="true">Jump to:</span>
        {[
          ["quote", "Quote"],
          ["forecast", "Forecast"],
          ["analytics", "Analytics"],
          ["events", "Events"],
          ["backtest", "Backtest"],
        ].map(([id, label]) => (
          <a key={id} href={`#research-${id}`} className="term-btn-sm">
            {label}
          </a>
        ))}
      </nav>
      <div id="research">
        <ForecastDetails symbol={decoded} />
      </div>
      {/* V2 compliant ads: one in-article slot below the research (slot index
          2 of the per-tier budget). Never inside table rows. */}
      <AdSlot slotId="forecast-inarticle" format="in-article" tier={tier} slotIndex={2} />
    </div>
  );
}

export { ForecastDetailsPage as default };
