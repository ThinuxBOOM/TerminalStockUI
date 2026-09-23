// Account page: simpler shell (not dense terminal).
// Sections: Profile / Preferences / Security / Connected services + billing
// status + Stripe portal. Auth via useAuth (guest = zero requests).

import React, { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { getBillingStatus, openPortal, redirectToUrl } from "../api/billing";
import { useAuth } from "../hooks/useAuth";
import Skeleton from "../components/Skeleton";

const HORIZON_KEY = "onemarket.prefs.default_horizon.v1";

function loadHorizon(fallback = 21) {
  try {
    const raw = localStorage.getItem(HORIZON_KEY);
    const n = Number(raw);
    if ([1, 7, 14, 21].includes(n)) return n;
  } catch {
    // ignore
  }
  return fallback;
}

function AccountPage() {
  const { isAuthenticated, userId, email, tier, logout, refresh } = useAuth();
  const navigate = useNavigate();
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(false);
  const [portalBusy, setPortalBusy] = useState(false);
  const [error, setError] = useState(null);
  const [defaultHorizon, setDefaultHorizon] = useState(() => loadHorizon(21));

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

  function saveHorizon(n) {
    setDefaultHorizon(n);
    try {
      localStorage.setItem(HORIZON_KEY, String(n));
    } catch {
      // ignore
    }
  }

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
    <main className="mx-auto max-w-2xl space-y-4" aria-label="Account">
      <div>
        <p className="term-label">ACCOUNT</p>
        <h1 className="mt-1 text-xl font-bold text-term-text">Your account</h1>
        <p className="mt-1 text-xs text-term-muted">Simpler shell — profile, preferences, security, services and billing.</p>
      </div>

      <section className="term-panel min-w-0 p-4" aria-labelledby="account-profile">
        <h2 id="account-profile" className="term-label">PROFILE</h2>
        <dl className="mt-2 space-y-1 text-sm">
          <div className="flex justify-between gap-2">
            <dt className="text-term-muted">Email</dt>
            <dd className="min-w-0 truncate text-term-text">{email ?? "—"}</dd>
          </div>
          <div className="flex justify-between gap-2">
            <dt className="text-term-muted">User ID</dt>
            <dd className="term-num min-w-0 truncate text-term-muted" title={String(userId ?? "")}>{userId ?? "—"}</dd>
          </div>
          <div className="flex justify-between gap-2">
            <dt className="text-term-muted">Plan</dt>
            <dd className="font-bold text-term-green">{String(liveTier ?? "Free")}</dd>
          </div>
        </dl>
      </section>

      <section className="term-panel min-w-0 p-4" aria-labelledby="account-prefs">
        <h2 id="account-prefs" className="term-label">PREFERENCES · THIS BROWSER</h2>
        <p className="mt-1 text-[11px] text-term-muted">Stored locally — no account change, no network request.</p>
        <div className="mt-2">
          <p className="text-xs text-term-muted" id="pref-horizon">Default forecast horizon</p>
          <div className="mt-1 flex flex-wrap gap-1.5" role="group" aria-labelledby="pref-horizon">
            {[1, 7, 14, 21].map((h) => (
              <button
                key={h}
                type="button"
                aria-pressed={defaultHorizon === h}
                onClick={() => saveHorizon(h)}
                className={defaultHorizon === h ? "term-btn px-2 py-1 text-xs" : "term-btn-ghost px-2 py-1 text-xs"}
              >
                {h}D
              </button>
            ))}
          </div>
        </div>
      </section>

      <section className="term-panel min-w-0 p-4" aria-labelledby="account-security">
        <h2 id="account-security" className="term-label">SECURITY</h2>
        <p className="mt-1 text-xs text-term-muted">Session is token-based; signing out clears this browser only.</p>
        <div className="mt-2 flex flex-wrap gap-2">
          <button
            type="button"
            className="term-btn-ghost text-xs"
            onClick={() => {
              void logout().then(() => navigate("/", { replace: true }));
            }}
          >
            SIGN OUT
          </button>
          <Link to="/login?next=/account" className="term-btn-ghost text-xs">
            SWITCH ACCOUNT →
          </Link>
        </div>
      </section>

      <section className="term-panel min-w-0 p-4" aria-labelledby="account-services">
        <h2 id="account-services" className="term-label">CONNECTED SERVICES</h2>
        <ul className="mt-2 space-y-1 text-xs">
          <li className="flex flex-wrap items-center justify-between gap-2 border-b border-term-border pb-1">
            <span className="text-term-muted">Stripe billing</span>
            <span className="text-term-green">● Connected via portal</span>
          </li>
          <li className="flex flex-wrap items-center justify-between gap-2 border-b border-term-border pb-1">
            <span className="text-term-muted">AI providers</span>
            <Link to="/providers" className="text-term-green hover:underline">Manage keys →</Link>
          </li>
          <li className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-term-muted">Watchlist</span>
            <Link to="/watchlist" className="text-term-green hover:underline">Open My List →</Link>
          </li>
        </ul>
      </section>

      <section className="term-panel min-w-0 p-4" aria-labelledby="account-billing">
        <h2 id="account-billing" className="term-label">BILLING STATUS</h2>
        {loading ? (
          <div className="mt-2"><Skeleton label="checking subscription…" lines={3} /></div>
        ) : (
          <dl className="mt-2 space-y-1 text-sm">
            <div className="flex justify-between gap-2">
              <dt className="text-term-muted">Subscription</dt>
              <dd className="term-num text-term-text">{subStatus ?? "—"}</dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt className="text-term-muted">Stripe customer</dt>
              <dd className="term-num min-w-0 truncate text-term-muted">{status?.stripe_customer_id ?? "—"}</dd>
            </div>
          </dl>
        )}
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
          <Link to="/pricing" className="term-btn-ghost text-xs">
            CHANGE PLAN →
          </Link>
        </div>
        <p className="mt-2 text-[11px] text-term-muted">
          Billing runs on Stripe (card never touches our servers). Cancel or
          change plans in the portal — the webhook syncs your tier automatically.
        </p>
      </section>
    </main>
  );
}

export { AccountPage as default };
