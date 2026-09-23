import React from "react";
import { useSearchParams } from "react-router-dom";
import LandingShell from "../features/landing/LandingShell.jsx";
import "../features/landing/landing.css";

// ---------------------------------------------------------------------------
// Welcome / landing page — Phase 5 premium landing.
// Public route (/welcome), no auth, no gating. Renders full-bleed standalone
// through its own LandingShell (no dependency on AppShell/Layout sidebar).
// ?from=security/<symbol> (or ?from=/...) is preserved as a friendly
// "back to where you were" link so the Security Brief breadcrumb keeps
// working. Anchor ids #how #features #markets #pricing #faq live in the
// story sections for deep links.
// ---------------------------------------------------------------------------

function WelcomePage() {
  const [params] = useSearchParams();

  const from = params.get("from") || params.get("symbol") || "";
  const returnHref = from
    ? from.startsWith("/") || from.startsWith("security/")
      ? from.startsWith("/")
        ? from
        : `/${from}`
      : `/security/${encodeURIComponent(from)}`
    : "";

  return (
    <div className="landing-bleed">
      <LandingShell fromHref={returnHref} fromLabel={from} />
    </div>
  );
}

export { WelcomePage as default };
