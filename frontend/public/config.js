// OneMarket runtime config — loaded by index.html BEFORE the app bundle.
//
// This is the NO-REBUILD knob for the backend URL when you host locally
// (LAN IP, custom ports, Docker on another host, static file server, ...).
// Edit this file and refresh the browser. Served as /config.js from the
// same origin as the app, so it also works from `frontend/dist` after
// `npm run build` (edit `dist/config.js`, or mount your own over it).
//
// Priority (first non-empty wins):
//   1. ?api=<url> query param (persisted to this browser)
//   2. localStorage "onemarket:api-base-url" (Provider Settings page UI)
//   3. THIS FILE (API_BASE_URL below)
//   4. VITE_API_BASE_URL baked in at build time
//   5. smart default (same-origin on https/remote hosts, localhost:8000 locally)
//
// Leave API_BASE_URL as "" to keep the default behaviour.
window.__ONEMARKET_CONFIG__ = {
  API_BASE_URL: "",
};
