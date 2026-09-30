// Backend base URL.
//
// Production serves the SPA and the API from the same origin (Caddy routes
// /api/* to the backend), so the default is "" (relative URLs). `vite dev`
// proxies /api to the backend too. VITE_API_BASE_URL exists only for a
// split frontend/backend setup and is fixed at build time: there is
// deliberately no runtime override (query string, localStorage, config.js),
// because a URL anyone can set would let a crafted link send a user's
// password and tokens to another server.

function normalizeHttpUrl(raw) {
  if (typeof raw !== "string") return null;
  const trimmed = raw.trim().replace(/\/+$/, "");
  if (!trimmed || !/^https?:\/\/[^\s]+$/i.test(trimmed)) return null;
  try {
    const u = new URL(trimmed);
    return u.protocol === "http:" || u.protocol === "https:" ? trimmed : null;
  } catch {
    return null;
  }
}

function resolveApiBaseUrl() {
  let raw = "";
  try {
    raw = import.meta.env?.VITE_API_BASE_URL ?? "";
  } catch {
    raw = "";
  }
  return normalizeHttpUrl(raw) ?? "";
}

const API_BASE_URL = resolveApiBaseUrl();

export { API_BASE_URL, normalizeHttpUrl, resolveApiBaseUrl };
