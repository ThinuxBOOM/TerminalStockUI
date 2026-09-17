// EXAMPLE — copy to `config.js` (same folder) and adjust. NOT loaded by the
// app under this name; `config.js` is what index.html references.
//
// Scenario A — backend on another machine on your LAN, frontend on this one:
//   API_BASE_URL: "http://192.168.1.20:8000",
//   then on the BACKEND host allow this frontend origin:
//     CORS_ORIGINS=http://192.168.1.10:5173   (or FRONTEND_URL=<same>)
//
// Scenario B — custom backend port (backend started with --port 9000):
//   API_BASE_URL: "http://localhost:9000",
//
// Scenario C — Docker Compose on this machine with default ports:
//   API_BASE_URL: "http://localhost:8000",
//
// Scenario D — same-origin deploy (frontend + backend on one host, e.g. a
//   reverse proxy routing /api to the backend): leave "" — the app uses
//   relative URLs and no CORS setup is needed.
//
// Quick one-off without editing files: open the app with
//   http://localhost:5173/?api=http://192.168.1.20:8000
// and the URL is remembered in that browser (clear with ?api=clear).
window.__ONEMARKET_CONFIG__ = {
  API_BASE_URL: "http://192.168.1.20:8000",
};
