// V2 account page: live subscription status + Stripe Customer Portal
// (cancel / upgrade / downgrade) via POST /api/billing/portal.

import React, { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { getBillingStatus, openPortal, redirectToUrl } from "../api/billing";
import { useAuth } from "../hooks/useAuth";

function AccountPage() {
  const { isAuthenticated, email, tier, logout, refresh } = useAuth();
  const navigate = useNavigate();
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(false);
  const [portalBusy, setPortalBusy] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    if (!isAuthenticated) return;
    setLoading(true);
    setError(null);
    try {
      const s = await getBillingStatus();
      setStatus(s);
    } catch (err) {
      setError(err instanceof Error ? err.message : "status check failed");
    } finally {
      setLoading(false);
    }
  }, [isAuthenticated]);

  useEffect(() => {
    void load();
    void refresh().catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load]);

  async function onPortal() {
    setPortalBusy(true);
    setError(null);
    try {
      const url = await openPortal();
      redirectToUrl(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : "portal failed to open");
    } finally {
      setPortalBusy(false);
    }
  }

  if (!isAuthenticated) {
    return (
      <div className="mx-auto max-w-md text-center">
        <p className="term-label">ACCOUNT</p>
        <h1 className="mt-1 text-xl font-bold text-term-text">Sign in to manage your plan</h1>
        <p className="mt-1 text-sm text-term-muted">
          Guest browsing needs no account — signing in unlocks plans and billing.
        </p>
        <div className="mt-4 flex flex-wrap justify-center gap-2">
          <Link to="/login?next=/account" className="term-btn text-xs">
            SIGN IN →
          </Link>
          <Link to="/pricing" className="term-btn-ghost text-xs">
            SEE PLANS →
          </Link>
        </div>
      </div>
    );
  }

  const liveTier = status?.tier ?? tier;
  const subStatus = status?.subscription_status;

  return (
    <div className="mx-auto max-w-2xl">
      <p className="term-label">ACCOUNT</p>
      <h1 className="mt-1 text-xl font-bold text-term-text">Your subscription</h1>

      <section className="term-panel mt-4 p-4" aria-labelledby="account-status">
        <h2 id="account-status" className="term-label">STATUS</h2>
        <dl className="mt-2 space-y-1 text-sm">
          <div className="flex justify-between gap-2">
            <dt className="text-term-muted">Email</dt>
            <dd className="text-term-text">{email ?? "—"}</dd>
          </div>
          <div className="flex justify-between gap-2">
            <dt className="text-term-muted">Plan</dt>
            <dd className="font-bold text-term-green">{String(liveTier ?? "Free")}</dd>
          </div>
          <div className="flex justify-between gap-2">
            <dt className="text-term-muted">Subscription</dt>
            <dd className="text-term-text">
              {loading ? "checking…" : subStatus ?? "—"}
            </dd>
          </div>
        </dl>
        {error ? (
          <p className="mt-2 text-xs text-term-red" role="alert">
            {error}
          </p>
        ) : null}
        <div className="mt-3 flex flex-wrap gap-2">
          <button
            type="button"
            className="term-btn text-xs"
            disabled={portalBusy}
            onClick={() => void onPortal()}
          >
            {portalBusy ? "OPENING…" : "MANAGE / CANCEL →"}
          </button>
          <button type="button" className="term-btn-ghost text-xs" onClick={() => void load()}>
            REFRESH STATUS
          </button>
        </div>
        <p className="mt-2 text-[11px] text-term-muted">
          Billing runs on Stripe (card never touches our servers). Cancel or
          change plans in the portal — the webhook syncs your tier automatically.
        </p>
      </section>

      <div className="mt-4 flex flex-wrap gap-2 text-xs">
        <Link to="/pricing" className="term-btn-ghost text-xs">
          CHANGE PLAN →
        </Link>
        <button
          type="button"
          className="term-btn-ghost text-xs"
          onClick={() => {
            void logout().then(() => navigate("/", { replace: true }));
          }}
        >
          SIGN OUT
        </button>
      </div>
    </div>
  );
}

export { AccountPage as default };
