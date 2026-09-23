import React, { Suspense, lazy } from "react";
import { Link } from "react-router-dom";
import LandingNav from "./LandingNav.jsx";
import HeroSection from "./HeroSection.jsx";

/* LandingShell: FloatingNav + Hero + ProductStory + InteractiveDemo +
 * Forecast + PlainEnglish + Watchlist + Markets + Research + Provenance +
 * Workflow + CTA + Footer. No terminal sidebar anywhere.
 * Below-fold stories load lazily so hero + first demo stay prioritized. */
const ProductStory = lazy(() => import("./ProductStory.jsx"));
const InteractiveTerminalDemo = lazy(() => import("./InteractiveTerminalDemo.jsx"));
const ForecastStory = lazy(() => import("./ForecastStory.jsx"));
const PlainEnglishStory = lazy(() => import("./PlainEnglishStory.jsx"));
const WatchlistStory = lazy(() => import("./WatchlistStory.jsx"));
const MarketsStory = lazy(() => import("./MarketsStory.jsx"));
const ResearchStory = lazy(() => import("./ResearchStory.jsx"));
const ProvenanceStory = lazy(() => import("./ProvenanceStory.jsx"));
const WorkflowStory = lazy(() => import("./WorkflowStory.jsx"));
const FinalCTA = lazy(() => import("./FinalCTA.jsx"));
const LandingFooter = lazy(() => import("./LandingFooter.jsx"));

function StoryFallback() {
  return (
    <div className="landing-inner" aria-hidden="true">
      <div className="lp-fallback">Loading section…</div>
    </div>
  );
}

function LandingShell({ fromHref, fromLabel }) {
  return (
    <div className="landing" id="top">
      <LandingNav fromHref={fromHref} fromLabel={fromLabel} />
      {fromHref ? (
        <div className="landing-inner">
          <p className="lp-backlink" role="status" style={{ paddingTop: 12 }}>
            You came from <code>{fromLabel}</code> — <Link to={fromHref} style={{ color: "#3ddc84", fontWeight: 700 }}>jump back to it →</Link>
          </p>
        </div>
      ) : null}
      <main>
        <HeroSection />
        <Suspense fallback={<StoryFallback />}>
          <ProductStory />
        </Suspense>
        <Suspense fallback={<StoryFallback />}>
          <InteractiveTerminalDemo />
        </Suspense>
        <Suspense fallback={<StoryFallback />}>
          <ForecastStory />
        </Suspense>
        <Suspense fallback={<StoryFallback />}>
          <PlainEnglishStory />
        </Suspense>
        <Suspense fallback={<StoryFallback />}>
          <WatchlistStory />
        </Suspense>
        <Suspense fallback={<StoryFallback />}>
          <MarketsStory />
        </Suspense>
        <Suspense fallback={<StoryFallback />}>
          <ResearchStory />
        </Suspense>
        <Suspense fallback={<StoryFallback />}>
          <ProvenanceStory />
        </Suspense>
        <Suspense fallback={<StoryFallback />}>
          <WorkflowStory />
        </Suspense>
        <Suspense fallback={<StoryFallback />}>
          <FinalCTA />
        </Suspense>
      </main>
      <Suspense fallback={null}>
        <LandingFooter />
      </Suspense>
    </div>
  );
}

export { LandingShell as default };
