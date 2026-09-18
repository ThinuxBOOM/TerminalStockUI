// V2 Stripe billing client. Card data never touches our server — checkout
// and cancel/upgrade/downgrade happen on Stripe-hosted pages; the webhook
// (backend) is the source of truth for tier. These helpers only open the
// Stripe sessions and read the live status row.

import { api } from "./client";

function normalizeTierParam(tier) {
  const s = String(tier ?? "").trim().toLowerCase();
  return ["silver", "gold", "platinum"].includes(s) ? s : null;
}

// Authed POST /api/billing/checkout {tier} -> {url}. Caller redirects the
// browser to the Stripe-hosted Checkout page.
async function startCheckout(tier) {
  const clean = normalizeTierParam(tier);
  if (!clean) throw new Error(`unknown tier for checkout: ${String(tier)}`);
  const { data } = await api.post("/api/billing/checkout", { tier: clean });
  const url = data?.url ?? data?.checkout_url ?? null;
  if (typeof url !== "string" || url === "") {
    throw new Error("checkout session created without a redirect url");
  }
  return url;
}

// Authed POST /api/billing/portal -> {url} (Stripe Customer Portal).
async function openPortal() {
  const { data } = await api.post("/api/billing/portal", {});
  const url = data?.url ?? data?.portal_url ?? null;
  if (typeof url !== "string" || url === "") {
    throw new Error("portal session created without a redirect url");
  }
  return url;
}

// Authed GET /api/billing/status -> {tier, subscription_status, ...}.
async function getBillingStatus(opts) {
  const signal = opts?.signal;
  const { data } = await api.get("/api/billing/status", {
    timeout: 15000,
    ...(signal ? { signal } : {}),
  });
  const d = data ?? {};
  return {
    tier: String(d.tier ?? "free"),
    subscription_status:
      typeof d.subscription_status === "string" ? d.subscription_status : null,
    stripe_customer_id:
      typeof d.stripe_customer_id === "string" ? d.stripe_customer_id : null,
    raw: d,
  };
}

function redirectToUrl(url) {
  try {
    if (typeof window !== "undefined" && window.location && typeof url === "string" && url !== "") {
      window.location.assign(url);
    }
  } catch {
    // never throw from navigation
  }
}

export { getBillingStatus, normalizeTierParam, openPortal, redirectToUrl, startCheckout };
