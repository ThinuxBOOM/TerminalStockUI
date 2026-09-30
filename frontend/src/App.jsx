import React, { Suspense, lazy } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import AppShell from "./components/AppShell/AppShell";
import ErrorBoundary from "./components/ErrorBoundary";
import Skeleton from "./components/Skeleton";
import RequireAuth from "./auth/RequireAuth";
import HomePage from "./pages/HomePage";
import NotFoundPage from "./pages/NotFoundPage";

// Route-level code splitting: each page (and its heavy deps) loads on demand.
const SearchPage = lazy(() => import("./pages/SearchPage"));
const ScreenerPage = lazy(() => import("./pages/ScreenerPage"));
const SecurityBriefPage = lazy(() => import("./pages/SecurityBriefPage"));
const ForecastDetailsPage = lazy(() => import("./pages/ForecastDetailsPage"));
const ProviderSettingsPage = lazy(() => import("./pages/ProviderSettingsPage"));
const BacktestLabPage = lazy(() => import("./pages/BacktestLabPage"));
const WatchlistPage = lazy(() => import("./pages/WatchlistPage"));
const WelcomePage = lazy(() => import("./pages/WelcomePage"));
const LoginPage = lazy(() => import("./pages/LoginPage"));
const AccountPage = lazy(() => import("./pages/AccountPage"));

function WelcomeRedirect() {
  const location = useLocation();
  return <Navigate to={{ pathname: "/", search: location.search }} replace />;
}

const guarded = (element, opts = {}) => <RequireAuth admin={opts.admin === true}>{element}</RequireAuth>;

// "/" is the public landing page and "/login" signs in; everything else is
// the terminal, which requires an account.
function App() {
  const location = useLocation();
  const path = location.pathname;
  const bare = path === "/" || path === "/login" || path.startsWith("/welcome");
  const routes = (
    <Routes>
      <Route path="/" element={<WelcomePage />} />
      <Route path="/welcome/*" element={<WelcomeRedirect />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/app" element={guarded(<HomePage />)} />
      <Route path="/account" element={guarded(<AccountPage />)} />
      <Route path="/search" element={guarded(<SearchPage />)} />
      <Route path="/screener" element={guarded(<ScreenerPage />)} />
      <Route path="/security/:symbol" element={guarded(<SecurityBriefPage />)} />
      <Route path="/forecast/:symbol" element={guarded(<ForecastDetailsPage />)} />
      <Route path="/providers" element={guarded(<ProviderSettingsPage />)} />
      <Route path="/backtest" element={guarded(<BacktestLabPage />)} />
      <Route path="/watchlist" element={guarded(<WatchlistPage />)} />
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
  return (
    <ErrorBoundary>
      <Suspense fallback={<Skeleton label="loading page…" lines={6} />}>
        {bare ? routes : <AppShell>{routes}</AppShell>}
      </Suspense>
    </ErrorBoundary>
  );
}

export { App as default };
