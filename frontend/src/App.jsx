import React, { Suspense, lazy } from "react";
import { Routes, Route } from "react-router-dom";
import ErrorBoundary from "./components/ErrorBoundary";
import Layout from "./components/Layout";
import Skeleton from "./components/Skeleton";
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
const LoginStubPage = lazy(() => import("./pages/LoginStubPage"));
function App() {
  return /* @__PURE__ */ React.createElement(Layout, null, /* @__PURE__ */ React.createElement(ErrorBoundary, null, /* @__PURE__ */ React.createElement(Suspense, { fallback: /* @__PURE__ */ React.createElement(Skeleton, { label: "loading page…", lines: 6 }) }, /* @__PURE__ */ React.createElement(Routes, null, /* @__PURE__ */ React.createElement(Route, { path: "/", element: /* @__PURE__ */ React.createElement(HomePage, null) }), /* @__PURE__ */ React.createElement(Route, { path: "/welcome", element: /* @__PURE__ */ React.createElement(WelcomePage, null) }), /* @__PURE__ */ React.createElement(Route, { path: "/login", element: /* @__PURE__ */ React.createElement(LoginStubPage, null) }), /* @__PURE__ */ React.createElement(Route, { path: "/search", element: /* @__PURE__ */ React.createElement(SearchPage, null) }), /* @__PURE__ */ React.createElement(Route, { path: "/screener", element: /* @__PURE__ */ React.createElement(ScreenerPage, null) }), /* @__PURE__ */ React.createElement(Route, { path: "/security/:symbol", element: /* @__PURE__ */ React.createElement(SecurityBriefPage, null) }), /* @__PURE__ */ React.createElement(Route, { path: "/forecast/:symbol", element: /* @__PURE__ */ React.createElement(ForecastDetailsPage, null) }), /* @__PURE__ */ React.createElement(Route, { path: "/providers", element: /* @__PURE__ */ React.createElement(ProviderSettingsPage, null) }), /* @__PURE__ */ React.createElement(Route, { path: "/backtest", element: /* @__PURE__ */ React.createElement(BacktestLabPage, null) }), /* @__PURE__ */ React.createElement(Route, { path: "/watchlist", element: /* @__PURE__ */ React.createElement(WatchlistPage, null) }), /* @__PURE__ */ React.createElement(Route, { path: "*", element: /* @__PURE__ */ React.createElement(NotFoundPage, null) })))));
}
export { App as default };
