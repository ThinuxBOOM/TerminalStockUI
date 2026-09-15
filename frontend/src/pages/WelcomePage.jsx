import React from "react";
import { Link, useSearchParams } from "react-router-dom";
import { PLAN_TIERS, SUPPORTED_INDICATORS, TIER_FEATURES, loadFavoriteIndicators } from "../api/client";
import { quotaNoteFor } from "../api/authStub";
import useCurrentUserStub from "../hooks/useCurrentUserStub";
import { DeepResearchStub } from "../components/ResearchSection";

// Welcome / onboarding (future-prep interface only — no auth, no gating).
// Links indicator-favorites onboarding (?from=security/<symbol> carries the
// return path), tier overview, and product tour. Every CTA stays unlocked.
function WelcomePage() {
  const [params] = useSearchParams();
  const from = params.get("from") || params.get("symbol") || "";
  const { tier } = useCurrentUserStub();
  const favorites = loadFavoriteIndicators(undefined, ["SMA20", "EMA12", "RSI14"]);
  const returnHref = from
    ? from.startsWith("/") || from.startsWith("security/")
      ? from.startsWith("/") ? from : `/${from}`
      : `/security/${encodeURIComponent(from)}`
    : "/";

  return (
    <div className="mx-auto max-w-3xl">
      <p className="text-[11px] tracking-widest text-term-muted">WELCOME · ONBOARDING (STUB — NO AUTH)</p>
      <h1 className="mt-1 text-xl font-bold text-term-text">Welcome to OneMarket Terminal</h1>
      <p className="mt-1 text-sm text-term-muted">
        Deterministic analytics are the source of truth. AI opinions are bounded and capped at 20%. Not investment
        advice.
      </p>

      <section className="term-panel mt-4 p-4" aria-labelledby="welcome-indicators">
        <h2 id="welcome-indicators" className="term-label">1 · Indicator favorites (this browser only)</h2>
        <p className="mt-1 text-xs text-term-muted">
          Your overlay picks persist under <code>indicators:guest</code> (localStorage) until auth lands. No account
          needed — charts never gate on this.
        </p>
        <div className="mt-2 flex flex-wrap gap-1.5" role="list" aria-label="supported indicators">
          {SUPPORTED_INDICATORS.map((name) => (
            <span
              key={name}
              role="listitem"
              className={`rounded border px-2 py-0.5 text-[11px] ${favorites.includes(name) ? "border-term-green text-term-green" : "border-term-border text-term-muted"}`}
              title={favorites.includes(name) ? "in your favorites" : "available overlay"}
            >
              {name}
            </span>
          ))}
        </div>
        <p className="mt-2 text-[11px] text-term-muted">
          Favorites now: {favorites.length > 0 ? favorites.join(", ") : "none yet — toggle overlays on any Security Brief."}
        </p>
        <div className="mt-2 flex flex-wrap gap-2 text-xs">
          <Link to={returnHref} className="term-btn text-xs">
            {from ? `BACK TO ${String(from).toUpperCase()} →` : "OPEN A SECURITY BRIEF →"}
          </Link>
          <Link to="/screener" className="term-btn-ghost text-xs">SCREENER →</Link>
        </div>
      </section>

      <section className="term-panel mt-4 p-4" aria-labelledby="welcome-tiers">
        <h2 id="welcome-tiers" className="term-label">2 · Plans (reference only — nothing is gated)</h2>
        <p className="mt-1 text-xs text-term-muted">
          Viewing as <b className="text-term-text">{tier}</b> (guest stub). Switching tiers on the{" "}
          <Link to="/login" className="text-term-green hover:underline">login stub</Link> only changes labels — every
          feature stays unlocked.
        </p>
        <ul className="mt-2 grid gap-2 text-xs sm:grid-cols-2">
          {PLAN_TIERS.map((t) => (
            <li key={t} className={`rounded border p-2 ${t === tier ? "border-term-green" : "border-term-border"}`}>
              <p className="font-bold text-term-text">{t}{t === tier && <span className="ml-1 text-[10px] font-normal text-term-green">(viewing as)</span>}</p>
              <ul className="mt-1 space-y-0.5 text-term-muted">
                {Object.entries(TIER_FEATURES).map(([feature, meta]) => (
                  <li key={feature} title={quotaNoteFor(t, feature)}>
                    {feature} — min {meta.minTier}
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
        <div className="mt-2 grid gap-2 md:grid-cols-2">
          <DeepResearchStub locked={false} tier={tier} feature="Deep Research" />
          <DeepResearchStub locked={false} tier={tier} feature="Report" />
        </div>
      </section>

      <section className="term-panel mt-4 p-4" aria-labelledby="welcome-tour">
        <h2 id="welcome-tour" className="term-label">3 · Tour (60 seconds)</h2>
        <ol className="mt-2 list-decimal space-y-1 pl-5 text-sm text-term-muted">
          <li><Link to="/search" className="text-term-green hover:underline">Search</Link> a ticker (AAPL, 600519.SS, ASML.AS).</li>
          <li>Open the <b className="text-term-text">Security Brief</b> — forecast (A) deterministic + chart overlays.</li>
          <li>Open <b className="text-term-text">Forecast Details</b> for the full (A)/(B) research + audit trail.</li>
          <li>Expand <b className="text-term-text">Market indices &amp; Top-20</b> and <b className="text-term-text">Liquidation proxy</b> on Home.</li>
        </ol>
        <div className="mt-3 flex flex-wrap gap-2 text-xs">
          <Link to="/" className="term-btn-ghost text-xs">← HOME</Link>
          <Link to="/login" className="term-btn-ghost text-xs">LOGIN STUB →</Link>
        </div>
      </section>
    </div>
  );
}

export { WelcomePage as default };
