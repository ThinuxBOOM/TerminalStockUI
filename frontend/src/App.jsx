import React, { Suspense, lazy } from "react";
import { Routes, Route, useLocation } from "react-router-dom";
import ErrorBoundary from "./components/ErrorBoundary";
import Skeleton from "./components/Skeleton";
import UpgradeModal from "./components/UpgradeModal";
import AppShell from "./components/AppShell/AppShell";
import HomePage from "./pages/HomePage";
import NotFoundPage from "./pages/NotFoundPage";
// Route-level code splitting: each page (and its heavy deps — charts,
// tables, provider settings) loads on demand instead of bloating the
// initial bundle. Home + 404 stay eager for instant LCP.
const SearchPage = lazy(() => import("./pages/SearchPage"));
const ScreenerPage = lazy(() => import("./pages/ScreenerPage"));
const SecurityBriefPage = lazy(() => import("./pages/SecurityBriefPage"));
const ForecastDetailsPage = lazy(() => import("./pages/ForecastDetailsPage"));
const ProviderSettingsPage = lazy(() => import("./pages/ProviderSettingsPage"));
const BacktestLabPage = lazy(() => import("./pages/BacktestLabPage"));
const WatchlistPage = lazy(() => import("./pages/WatchlistPage"));
const WelcomePage = lazy(() => import("./pages/WelcomePage"));
// V2 real auth + billing routes (lazy like the rest).
const LoginPage = lazy(() => import("./pages/LoginPage"));
const PricingPage = lazy(() => import("./pages/PricingPage"));
const CheckoutSuccessPage = lazy(() => import("./pages/CheckoutSuccessPage"));
const AccountPage = lazy(() => import("./pages/AccountPage"));

// Shell switch (BrowserRouter is provided by main.jsx): /welcome renders
// WITHOUT any AppShell chrome — WelcomePage owns its full cinematic shell
// (src/features/landing/LandingShell.jsx: FloatingNav + Hero + stories +
// Footer). Wrapping it here would double header/footer and constrain width.
// Every other terminal route renders in AppShell.
// All route paths and ?q=/?symbol=/?from=/?next=/?session_id= contracts are
// owned by the pages below and unchanged. lazy(), ErrorBoundary, Suspense,
// UpgradeModal, and the AdSlot budget (inside AppShell) are preserved.
function ShellRoutes() {
  const location = useLocation();
  const isLanding = location.pathname === "/welcome" || location.pathname.startsWith("/welcome/");
  const routes = (
    <Routes>
      <Route path="/" element={<HomePage />} />
      <Route path="/welcome" element={<WelcomePage />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/pricing" element={<PricingPage />} />
      <Route path="/checkout/success" element={<CheckoutSuccessPage />} />
      <Route path="/account" element={<AccountPage />} />
      <Route path="/search" element={<SearchPage />} />
      <Route path="/screener" element={<ScreenerPage />} />
      <Route path="/security/:symbol" element={<SecurityBriefPage />} />
      <Route path="/forecast/:symbol" element={<ForecastDetailsPage />} />
      <Route path="/providers" element={<ProviderSettingsPage />} />
      <Route path="/backtest" element={<BacktestLabPage />} />
      <Route path="/watchlist" element={<WatchlistPage />} />
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
  return (
    <ErrorBoundary>
      <Suspense fallback={<Skeleton label="loading page…" lines={6} />}>
        {isLanding ? routes : <AppShell>{routes}</AppShell>}
      </Suspense>
    </ErrorBoundary>
  );
}

function App() {
  return (
    <>
      <ShellRoutes />
      {/* Global 402 upsell surface (axios 402 -> event -> this modal). */}
      <UpgradeModal />
    </>
  );
}
export { App as default };
