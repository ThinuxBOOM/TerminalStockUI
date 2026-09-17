import React from "react";
import BackendConnectionCard from "../components/BackendConnectionCard";
import ProviderSettings from "../features/providers/ProviderSettings";
function ProviderSettingsPage() {
  return /* @__PURE__ */ React.createElement("div", { className: "space-y-4" }, /* @__PURE__ */ React.createElement("h1", { className: "mb-3 text-sm tracking-widest text-term-muted" }, "PROVIDER SETTINGS \xB7 KEYS / PROFILES / HEALTH"), /* @__PURE__ */ React.createElement(BackendConnectionCard, null), /* @__PURE__ */ React.createElement(ProviderSettings, null));
}
export { ProviderSettingsPage as default };
