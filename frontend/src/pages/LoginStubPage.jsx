import React, { useState } from "react";
import { Link } from "react-router-dom";
import { TIER_ORDER, canUseFeatureStub, getCurrentUserStub } from "../api/authStub";
import { TIER_FEATURES } from "../api/client";
import useCurrentUserStub from "../hooks/useCurrentUserStub";

// Login stub (future-prep interface only — NO real auth, NO billing).
// Explains guest mode, offers a tier preview selector (localStorage only,
// never gates), and documents where real auth will plug in.
function LoginStubPage() {
  const { userId, tier, isGuest, setTier, setUserId } = useCurrentUserStub();
  const [draftId, setDraftId] = useState(userId ?? "");
  const snapshot = getCurrentUserStub();

  return (
    <div className="mx-auto max-w-2xl">
      <p className="text-[11px] tracking-widest text-term-muted">LOGIN · STUB (NO AUTH ENFORCED)</p>
      <h1 className="mt-1 text-xl font-bold text-term-text">You&apos;re browsing as guest</h1>
      <p className="mt-1 text-sm text-term-muted">
        Auth is not implemented — every page works without login. This stub previews the future
        user_id/tier surface (cache keys already namespace <code>u:{String(snapshot.userId ?? "guest")}</code> /{" "}
        <code>t:{String(snapshot.tier ?? "free").toLowerCase()}</code>) so plans can land without key migration.
      </p>

      <section className="term-panel mt-4 p-4" aria-labelledby="login-tier">
        <h2 id="login-tier" className="term-label">Tier preview (labels only — nothing locks)</h2>
        <div className="mt-2 flex flex-wrap gap-1" role="group" aria-label="preview tier">
          {TIER_ORDER.map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setTier(t)}
              aria-pressed={tier === t}
              className={tier === t ? "term-btn text-xs" : "term-btn-ghost text-xs"}
            >
              {t}
            </button>
          ))}
        </div>
        <ul className="mt-2 space-y-1 text-xs text-term-muted">
          {Object.entries(TIER_FEATURES).map(([feature, meta]) => (
            <li key={feature}>
              {meta.lockedIcon} {feature} — min {meta.minTier} ·{" "}
              {canUseFeatureStub(tier, feature) ? (
                <span className="text-term-green">would be unlocked</span>
              ) : (
                <span className="text-term-amber">would lock (stub shows unlocked anyway)</span>
              )}{" "}
              · viewing as {tier} {isGuest ? "(guest)" : ""}
            </li>
          ))}
        </ul>
      </section>

      <section className="term-panel mt-4 p-4" aria-labelledby="login-user">
        <h2 id="login-user" className="term-label">Future user_id (optional label)</h2>
        <form
          className="mt-2 flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            setUserId(draftId.trim() === "" ? null : draftId.trim());
          }}
        >
          <input
            className="term-input min-w-0 flex-1"
            value={draftId}
            onChange={(e) => setDraftId(e.target.value)}
            placeholder="preview user id (empty = guest)"
            aria-label="Preview user id"
            spellCheck={false}
            autoComplete="off"
          />
          <button className="term-btn shrink-0 text-xs" type="submit">SAVE</button>
        </form>
        <p className="mt-1 text-[11px] text-term-muted">
          Current: userId={userId ?? "guest"} · tier={tier} · isGuest={String(isGuest)}. Keys, quotas, and billing
          arrive with the future users DB — this page will become the real login then.
        </p>
      </section>

      <div className="mt-4 flex flex-wrap gap-2 text-xs">
        <Link to="/welcome" className="term-btn-ghost text-xs">← WELCOME</Link>
        <Link to="/app" className="term-btn-ghost text-xs">HOME →</Link>
      </div>
    </div>
  );
}

export { LoginStubPage as default };
