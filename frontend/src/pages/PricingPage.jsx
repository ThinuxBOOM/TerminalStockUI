// Pricing page: simpler shell (not dense terminal).
// Sections: Plan / Features / Billing. Stripe Checkout buttons
// (POST /api/billing/checkout). Prices/tiers shown here are marketing copy;
// the server-side price map is the source of truth and the webhook alone
// upgrades tiers.

import React, { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { normalizeTierParam, redirectToUrl, startCheckout } from "../api/billing";
import { useAuth } from "../hooks/useAuth";

const PLANS = [
  {
    tier: "silver",
    name: "Silver",
    blurb: "Deeper research reports + Top Picks screener.",
    features: ["Deep Research briefs", "Top Picks screener", "Extended history"],
    cta: "SUBSCRIBE · SILVER →",
  },
  {
    tier: "gold",
    name: "Gold",
    blurb: "Everything in Silver + backtests and higher limits.",
    features: ["Everything in Silver", "Backtest Lab scoring", "Higher rate limits"],
    cta: "SUBSCRIBE · GOLD →",
  },
  {
    tier: "platinum",
    name: "Platinum",
    blurb: "Everything in Gold + provider configuration and priority.",
    features: ["Everything in Gold", "Provider key config", "Priority queue"],
    cta: "SUBSCRIBE · PLATINUM →",
  },
];

function PricingPage() {
  const { isAuthenticated, tier } = useAuth();
  const navigate = useNavigate();
  const [busyTier, setBusyTier] = useState(null);
  const [error, setError] = useState(null);

  async function onCheckout(planTier) {
    const clean = normalizeTierParam(planTier);
    if (!clean) return;
    if (!isAuthenticated) {
      navigate(`/login?next=${encodeURIComponent("/pricing")}`);
      return;
    }
    setBusyTier(clean);
    setError(null);
    try {
      const url = await startCheckout(clean);
      redirectToUrl(url);
    } catch (err) {
      const status = err?.response?.status;
      if (status === 401) {
        navigate(`/login?next=${encodeURIComponent("/pricing")}`);
        return;
      }
      let msg = "Checkout failed. Retry — no charge was made.";
      try {
        const d = err?.response?.data;
        const detail =
          (typeof d?.detail === "string" && d.detail) ||
          (typeof d?.message === "string" && d.message);
        if (detail) msg = detail.slice(0, 300);
      } catch {
        // keep default
      }
      setError(msg);
    } finally {
      setBusyTier(null);
    }
  }

  return (
    <main className="mx-auto max-w-4xl space-y-6" aria-label="Pricing">
      <div>
        <p className="term-label text-center">PRICING · PLAN</p>
        <h1 className="mt-1 text-center text-xl font-extrabold text-term-text">
          Free to explore. Upgrade for deeper research.
        </h1>
        <p className="mx-auto mt-1 max-w-xl text-center text-xs leading-relaxed text-term-muted">
          Payments run on Stripe — your card never touches our servers. Current
          plan: <b className="text-term-text">{String(tier ?? "Free")}</b>.
        </p>
      </div>

      {error ? (
        <p className="term-panel mt-4 p-3 text-center text-xs text-term-red" role="alert">
          {error}
        </p>
      ) : null}

      <section aria-label="Plans">
        <ul className="mt-5 grid grid-cols-1 gap-3 md:grid-cols-3">
          <li className="term-panel-nested flex min-w-0 flex-col p-4">
            <h2 className="text-sm font-bold text-term-text">Starter</h2>
            <p className="term-num mt-1 text-2xl font-black text-term-text">
              $0 <span className="text-xs font-normal text-term-muted">· free forever</span>
            </p>
            <ul className="mt-3 flex-1 space-y-1.5 text-xs text-term-muted">
              <li>✓ Search every market</li>
              <li>✓ Plain-words forecasts + charts</li>
              <li>✓ My List watchlist</li>
            </ul>
            <Link to="/app" className="term-btn mt-4 text-center text-xs">
              START EXPLORING →
            </Link>
          </li>
          {PLANS.map((p) => (
            <li key={p.tier} className="flex min-w-0 flex-col rounded-lg border border-term-green bg-term-panel2 p-4 shadow-panel-lg">
              <h2 className="text-sm font-bold text-term-green">{p.name}</h2>
              <p className="mt-1 text-xs text-term-muted">{p.blurb}</p>
              <ul className="mt-2 space-y-1 text-xs text-term-muted">
                {p.features.map((feat) => (
                  <li key={feat}>✓ {feat}</li>
                ))}
              </ul>
              <button
                type="button"
                className="term-btn mt-4 text-xs"
                disabled={busyTier !== null}
                onClick={() => void onCheckout(p.tier)}
              >
                {busyTier === p.tier ? "OPENING STRIPE…" : p.cta}
              </button>
            </li>
          ))}
        </ul>
      </section>

      <section className="term-panel min-w-0 p-4" aria-labelledby="pricing-features">
        <h2 id="pricing-features" className="term-label">FEATURES · WHAT EACH PLAN UNLOCKS</h2>
        <div className="mt-2 overflow-x-auto">
          <table className="w-full text-xs">
            <caption className="sr-only">Plan feature comparison</caption>
            <thead>
              <tr className="text-left text-term-muted">
                <th scope="col" className="py-1 pr-2">Capability</th>
                <th scope="col" className="py-1 pr-2 text-center">Starter</th>
                <th scope="col" className="py-1 pr-2 text-center">Silver</th>
                <th scope="col" className="py-1 pr-2 text-center">Gold</th>
                <th scope="col" className="py-1 text-center">Platinum</th>
              </tr>
            </thead>
            <tbody>
              {[
                ["Search + forecasts + charts", "✓", "✓", "✓", "✓"],
                ["Watchlist", "✓", "✓", "✓", "✓"],
                ["Deep Research briefs", "—", "✓", "✓", "✓"],
                ["Backtest Lab scoring", "—", "—", "✓", "✓"],
                ["Provider key config", "—", "—", "—", "✓"],
              ].map(([cap, s0, s1, s2, s3]) => (
                <tr key={cap} className="border-t border-term-border">
                  <td className="py-1 pr-2 text-term-text">{cap}</td>
                  <td className="term-num py-1 pr-2 text-center text-term-muted">{s0}</td>
                  <td className="term-num py-1 pr-2 text-center text-term-muted">{s1}</td>
                  <td className="term-num py-1 pr-2 text-center text-term-muted">{s2}</td>
                  <td className="term-num py-1 text-center text-term-muted">{s3}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="term-panel min-w-0 p-4" aria-labelledby="pricing-billing">
        <h2 id="pricing-billing" className="term-label">BILLING · STRIPE ONLY</h2>
        <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-term-muted">
          <li>Checkout redirects to Stripe-hosted pages — card data never touches our servers.</li>
          <li>Manage or cancel anytime from <Link to="/account" className="text-term-green hover:underline">your account</Link> via the Customer Portal.</li>
          <li>Paying customers are upgraded by Stripe webhook — never by manual edits.</li>
        </ul>
        <p className="mt-3 text-center text-[11px] text-term-muted">
          Guests check out after signing in — you&apos;ll return to <span className="term-num">/pricing</span> automatically (<span className="term-num">?next=/pricing</span>).
        </p>
      </section>
    </main>
  );
}

export { PricingPage as default };
