// V2 pricing page: Stripe Checkout buttons (POST /api/billing/checkout).
// Prices/tiers shown here are marketing copy; the server-side price map is
// the source of truth and the webhook alone upgrades tiers.

import React, { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { normalizeTierParam, redirectToUrl, startCheckout } from "../api/billing";
import { useAuth } from "../hooks/useAuth";

const PLANS = [
  {
    tier: "silver",
    name: "Silver",
    blurb: "Deeper research reports + Top Picks screener.",
    cta: "SUBSCRIBE · SILVER →",
  },
  {
    tier: "gold",
    name: "Gold",
    blurb: "Everything in Silver + backtests and higher limits.",
    cta: "SUBSCRIBE · GOLD →",
  },
  {
    tier: "platinum",
    name: "Platinum",
    blurb: "Everything in Gold + provider configuration and priority.",
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
    <div className="mx-auto max-w-4xl">
      <p className="term-label text-center">PRICING</p>
      <h1 className="mt-1 text-center text-xl font-extrabold text-term-text">
        Free to explore. Upgrade for deeper research.
      </h1>
      <p className="mx-auto mt-1 max-w-xl text-center text-xs leading-relaxed text-term-muted">
        Payments run on Stripe — your card never touches our servers. Current
        plan: <b className="text-term-text">{String(tier ?? "Free")}</b>.
      </p>

      {error ? (
        <p className="term-panel mt-4 p-3 text-center text-xs text-term-red" role="alert">
          {error}
        </p>
      ) : null}

      <ul className="mt-5 grid gap-3 md:grid-cols-3">
        <li className="term-panel-nested flex flex-col p-4">
          <h2 className="text-sm font-bold text-term-text">Starter</h2>
          <p className="term-num mt-1 text-2xl font-black text-term-text">
            $0 <span className="text-xs font-normal text-term-muted">· free forever</span>
          </p>
          <ul className="mt-3 flex-1 space-y-1.5 text-xs text-term-muted">
            <li>✓ Search every market</li>
            <li>✓ Plain-words forecasts + charts</li>
            <li>✓ My List watchlist</li>
          </ul>
          <Link to="/" className="term-btn mt-4 text-center text-xs">
            START EXPLORING →
          </Link>
        </li>
        {PLANS.map((p) => (
          <li key={p.tier} className="flex flex-col rounded-lg border border-term-green bg-term-panel2 p-4 shadow-panel-lg">
            <h2 className="text-sm font-bold text-term-green">{p.name}</h2>
            <p className="mt-1 flex-1 text-xs text-term-muted">{p.blurb}</p>
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

      <p className="mt-4 text-center text-[11px] text-term-muted">
        Manage or cancel anytime from{" "}
        <Link to="/account" className="text-term-green hover:underline">
          your account
        </Link>
        . Paying customers are upgraded by Stripe webhook — never by manual edits.
      </p>
    </div>
  );
}

export { PricingPage as default };
