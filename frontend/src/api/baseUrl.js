// Shared backend base-URL resolution — single source of truth for every
// frontend API module (client.js, signals.js, news.js).
//
// Why this exists: `VITE_API_BASE_URL` is baked in at BUILD time, so a user
// hosting the stack locally (LAN IP, custom ports, Docker on another host)
// had to rebuild the frontend just to point at their backend. These layers
// let the URL change WITHOUT a rebuild (highest priority first):
//
//   1. `?api=<url>` query param — one-time override, persisted to localStorage.
//      `?api=clear` removes the stored override.
//   2. localStorage `onemarket:api-base-url` — set from the "Backend
//      connection" card on the Provider Settings page (no rebuild, survives
//      refresh; page reloads once on save to re-point every client).
//   3. `window.__ONEMARKET_CONFIG__.API_BASE_URL` — `frontend/public/config.js`,
//      shipped next to the built assets (`dist/config.js`). Edit the file (or
//      mount your own) and refresh — no rebuild. This is the recommended knob
//      for Docker / static hosting.
//   4. `import.meta.env.VITE_API_BASE_URL` — build-time value (Vite embeds
//      VITE_*). Still honoured; use it for production Bake-in deploys.
//   5. Smart default — "" (same-origin `/api`) on https or non-local http
//      hosts, `http://localhost:8000` on localhost / Node / tests.
//
// Only http(s) URLs are accepted; anything else is ignored (fail-closed to
// the next layer). All browser APIs are guarded so this stays import-safe in
// Node (vitest) and SSR.

const API_BASE_OVERRIDE_KEY = "onemarket:api-base-url";

const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);

function normalizeHttpUrl(raw) {
  if (typeof raw !== "string") return null;
  const trimmed = raw.trim().replace(/\/+$/, "");
  if (!trimmed) return null;
  if (!/^https?:\/\//i.test(trimmed)) return null;
  if (/\s/.test(trimmed)) return null;
  try {
    const u = new URL(trimmed);
    if (u.protocol !== "http:" && u.protocol !== "https:") return null;
    return trimmed;
  } catch {
    return null;
  }
}

function getWindow() {
  try {
    if (typeof window !== "undefined" && window) return window;
  } catch {
    // blocked window access (sandboxed iframe) — treat as headless
  }
  return null;
}

function getStorage() {
  // NOTE: bare `localStorage` reference throws ReferenceError in Node, and
  // property access can throw in locked-down browsers — hence the nesting.
  try {
    if (typeof localStorage !== "undefined" && localStorage) return localStorage;
  } catch {
    return null;
  }
  return null;
}

function readRuntimeConfig() {
  const win = getWindow();
  try {
    const cfg = win?.__ONEMARKET_CONFIG__;
    if (cfg && typeof cfg === "object") {
      return (
        normalizeHttpUrl(cfg.API_BASE_URL) ??
        normalizeHttpUrl(cfg.VITE_API_BASE_URL) ??
        null
      );
    }
  } catch {
    // ignore — fall through to build-time value
  }
  return null;
}

function readBuildTime() {
  try {
    const raw = import.meta.env?.VITE_API_BASE_URL;
    return normalizeHttpUrl(raw ?? "");
  } catch {
    return null;
  }
}

function readStoredOverride() {
  const store = getStorage();
  if (!store) return null;
  try {
    return normalizeHttpUrl(store.getItem(API_BASE_OVERRIDE_KEY) ?? "");
  } catch {
    return null;
  }
}

// Consumes `?api=` exactly once per page load: a valid URL is persisted and
// takes effect immediately; `?api=clear` (or empty) drops the override.
// Never throws; returns the consumed URL (or "cleared" sentinel as null with
// a cleared flag via the boolean second element).
function consumeQueryOverride() {
  const win = getWindow();
  if (!win?.location?.search) return { url: null, cleared: false };
  let param;
  try {
    param = new URLSearchParams(win.location.search).get("api");
  } catch {
    return { url: null, cleared: false };
  }
  if (param === null) return { url: null, cleared: false };
  const store = getStorage();
  if (param.trim() === "" || param.trim().toLowerCase() === "clear") {
    try {
      store?.removeItem(API_BASE_OVERRIDE_KEY);
    } catch {
      // storage unwritable — nothing to clear
    }
    return { url: null, cleared: true };
  }
  const url = normalizeHttpUrl(param);
  if (!url) return { url: null, cleared: false };
  try {
    store?.setItem(API_BASE_OVERRIDE_KEY, url);
  } catch {
    // storage unwritable (private mode) — still honour it for this load
    return { url, cleared: false };
  }
  return { url, cleared: false };
}

function smartDefault() {
  const win = getWindow();
  if (win?.location) {
    try {
      const { protocol, hostname } = win.location;
      if (protocol === "https:") return "";
      if (hostname && !LOCAL_HOSTS.has(hostname)) return "";
    } catch {
      // fall through to localhost default
    }
  }
  return "http://localhost:8000";
}

// Resolution result + which layer won (for the settings UI badge).
function resolveApiBaseUrlWithSource() {
  const query = consumeQueryOverride();
  if (query.url) return { url: query.url, source: "query" };
  const stored = readStoredOverride();
  // A ?api=clear on this load must win over the (just-removed) stored value.
  if (stored && !query.cleared) return { url: stored, source: "override" };
  const runtime = readRuntimeConfig();
  if (runtime) return { url: runtime, source: "runtime-config" };
  const build = readBuildTime();
  if (build) return { url: build, source: "build" };
  return { url: smartDefault(), source: "default" };
}

function resolveApiBaseUrl() {
  return resolveApiBaseUrlWithSource().url;
}

function setApiBaseUrlOverride(url) {
  const normalized = normalizeHttpUrl(url);
  if (!normalized) throw new Error("Backend URL must start with http:// or https://");
  const store = getStorage();
  if (!store) throw new Error("This browser blocks localStorage — edit dist/config.js instead");
  store.setItem(API_BASE_OVERRIDE_KEY, normalized);
  return normalized;
}

function clearApiBaseUrlOverride() {
  try {
    getStorage()?.removeItem(API_BASE_OVERRIDE_KEY);
  } catch {
    // already clear / unwritable — nothing to do
  }
}

export {
  API_BASE_OVERRIDE_KEY,
  clearApiBaseUrlOverride,
  normalizeHttpUrl,
  resolveApiBaseUrl,
  resolveApiBaseUrlWithSource,
  setApiBaseUrlOverride,
};
