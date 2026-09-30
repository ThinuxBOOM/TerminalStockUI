import React, { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";

function AccountPage() {
  const { user, isAdmin, logout } = useAuth();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);

  async function onSignOut() {
    setBusy(true);
    try {
      await logout();
    } finally {
      navigate("/", { replace: true });
    }
  }

  return (
    <main className="mx-auto max-w-2xl space-y-4" aria-label="Account">
      <div>
        <p className="term-label">ACCOUNT</p>
        <h1 className="mt-1 text-xl font-bold text-term-text">Your account</h1>
      </div>

      <section className="term-panel min-w-0 p-4" aria-labelledby="account-profile">
        <h2 id="account-profile" className="term-label">PROFILE</h2>
        <dl className="mt-2 space-y-1 text-sm">
          <div className="flex justify-between gap-2">
            <dt className="text-term-muted">Email</dt>
            <dd className="min-w-0 truncate text-term-text">{user?.email ?? "—"}</dd>
          </div>
          <div className="flex justify-between gap-2">
            <dt className="text-term-muted">Role</dt>
            <dd className="text-term-text">{isAdmin ? "Administrator" : "Member"}</dd>
          </div>
        </dl>
      </section>

      {isAdmin ? (
        <section className="term-panel min-w-0 p-4" aria-labelledby="account-admin">
          <h2 id="account-admin" className="term-label">ADMINISTRATION</h2>
          <p className="mt-1 text-xs text-term-muted">
            AI provider keys and monthly budgets apply to every user of this server.
          </p>
          <Link to="/providers" className="term-btn-ghost mt-2 inline-block text-xs">
            PROVIDER SETTINGS →
          </Link>
        </section>
      ) : null}

      <section className="term-panel min-w-0 p-4" aria-labelledby="account-security">
        <h2 id="account-security" className="term-label">SECURITY</h2>
        <p className="mt-1 text-xs text-term-muted">
          Signing out ends every session for this account, on all devices.
        </p>
        <button type="button" className="term-btn-ghost mt-2 text-xs" disabled={busy} onClick={() => void onSignOut()}>
          {busy ? "SIGNING OUT…" : "SIGN OUT"}
        </button>
      </section>
    </main>
  );
}

export { AccountPage as default };
