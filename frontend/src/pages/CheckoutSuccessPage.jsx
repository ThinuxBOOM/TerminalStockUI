// Post-Stripe return page. The webhook (not this page) upgrades the tier,
// so this view only confirms payment and points at /account for live status.

import React from "react";
import { Link, useSearchParams } from "react-router-dom";

function CheckoutSuccessPage() {
  const [params] = useSearchParams();
  const sessionId = (() => {
    try {
      return params.get("session_id") ?? "";
    } catch {
      return "";
    }
  })();

  return (
    <div className="mx-auto max-w-xl text-center">
      <p className="term-label">CHECKOUT COMPLETE</p>
      <h1 className="mt-1 text-xl font-extrabold text-term-text">
        🎉 Payment received — welcome aboard!
      </h1>
      <p className="mt-2 text-sm text-term-muted">
        Stripe is confirming your subscription. Your plan upgrades automatically
        within a minute — no manual steps needed.
      </p>
      {sessionId ? (
        <p className="mt-2 break-all font-mono text-[11px] text-term-muted">
          session {sessionId.slice(0, 24)}…
        </p>
      ) : null}
      <div className="mt-4 flex flex-wrap justify-center gap-2">
        <Link to="/account" className="term-btn text-xs">
          CHECK MY SUBSCRIPTION →
        </Link>
        <Link to="/app" className="term-btn-ghost text-xs">
          BACK TO TERMINAL →
        </Link>
      </div>
    </div>
  );
}

export { CheckoutSuccessPage as default };
