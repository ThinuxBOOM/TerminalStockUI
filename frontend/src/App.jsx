import React from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import Layout from "./components/Layout";
import HomePage from "./pages/HomePage";
import SearchPage from "./pages/SearchPage";
import ScreenerPage from "./pages/ScreenerPage";
import SecurityBriefPage from "./pages/SecurityBriefPage";
import ForecastDetailsPage from "./pages/ForecastDetailsPage";
import ProviderSettingsPage from "./pages/ProviderSettingsPage";
import BacktestLabPage from "./pages/BacktestLabPage";
import WatchlistPage from "./pages/WatchlistPage";
function App() {
  return /* @__PURE__ */ React.createElement(Layout, null, /* @__PURE__ */ React.createElement(Routes, null, /* @__PURE__ */ React.createElement(Route, { path: "/", element: /* @__PURE__ */ React.createElement(HomePage, null) }), /* @__PURE__ */ React.createElement(Route, { path: "/search", element: /* @__PURE__ */ React.createElement(SearchPage, null) }), /* @__PURE__ */ React.createElement(Route, { path: "/screener", element: /* @__PURE__ */ React.createElement(ScreenerPage, null) }), /* @__PURE__ */ React.createElement(Route, { path: "/security/:symbol", element: /* @__PURE__ */ React.createElement(SecurityBriefPage, null) }), /* @__PURE__ */ React.createElement(Route, { path: "/forecast/:symbol", element: /* @__PURE__ */ React.createElement(ForecastDetailsPage, null) }), /* @__PURE__ */ React.createElement(Route, { path: "/providers", element: /* @__PURE__ */ React.createElement(ProviderSettingsPage, null) }), /* @__PURE__ */ React.createElement(Route, { path: "/backtest", element: /* @__PURE__ */ React.createElement(BacktestLabPage, null) }), /* @__PURE__ */ React.createElement(Route, { path: "/watchlist", element: /* @__PURE__ */ React.createElement(WatchlistPage, null) }), /* @__PURE__ */ React.createElement(Route, { path: "*", element: /* @__PURE__ */ React.createElement(Navigate, { to: "/", replace: true }) })));
}
export { App as default };
