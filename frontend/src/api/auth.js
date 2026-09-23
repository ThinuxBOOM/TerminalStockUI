// V2 real auth client (JWT). Guest path stays in ./authStub.js — this module
// is the authed path only. Backend is the enforcer; the frontend only stores
// the token, injects `Authorization: Bearer`, and routes 401/402 for UX.
//
// Token storage: in-memory primary + localStorage fallback (survives reload).
// All storage access is guarded so node/vitest imports never throw.

import { api, setAuthErrorHandlers, setAuthTokenProvider } from "./client";

const STORAGE_TOKEN_KEY = "onemarket:auth:access_token";

let memoryToken = null;

function hasLocalStorage() {
  try {
    return typeof localStorage !== "undefined";
  } catch {
    return false;
  }
}

function getAccessToken() {
  if (typeof memoryToken === "string" && memoryToken !== "") return memoryToken;
  try {
    if (hasLocalStorage()) {
      const raw = localStorage.getItem(STORAGE_TOKEN_KEY);
      if (typeof raw === "string" && raw.trim() !== "") {
        memoryToken = raw.trim();
        return memoryToken;
      }
    }
  } catch {
    // storage unavailable — memory only
  }
  return null;
}

function setAccessToken(token) {
  const t = typeof token === "string" ? token.trim() : "";
  memoryToken = t === "" ? null : t;
  try {
    if (hasLocalStorage()) {
      if (memoryToken) localStorage.setItem(STORAGE_TOKEN_KEY, memoryToken);
      else localStorage.removeItem(STORAGE_TOKEN_KEY);
    }
  } catch {
    // memory only
  }
  return memoryToken;
}

function clearTokens() {
  memoryToken = null;
  try {
    if (hasLocalStorage()) localStorage.removeItem(STORAGE_TOKEN_KEY);
  } catch {
    // ignore
  }
  try {
    cachedMe = null;
  } catch {
    // ignore
  }
}

// Pure helper (node-testable): Authorization header for a token value.
function authHeaderFor(token) {
  const t = typeof token === "string" ? token.trim() : "";
  return t === "" ? {} : { Authorization: `Bearer ${t}` };
}

function extractToken(data) {
  const d = data ?? {};
  const t =
    d.access_token ?? d.accessToken ?? d.token ?? d?.data?.access_token ?? null;
  return typeof t === "string" && t.trim() !== "" ? t.trim() : null;
}

function normalizeMe(data) {
  const d = data ?? {};
  const tier = String(d.tier ?? "free").trim() || "free";
  return {
    id: d.id ?? d.user_id ?? null,
    email: typeof d.email === "string" ? d.email : null,
    tier,
    subscription_status:
      typeof d.subscription_status === "string" ? d.subscription_status : null,
    is_admin: d.is_admin === true,
  };
}

async function register(email, password) {
  const { data } = await api.post("/api/auth/register", { email, password });
  const token = extractToken(data);
  if (token) setAccessToken(token);
  cachedMe = null;
  return { data, token };
}

async function login(email, password) {
  const { data } = await api.post("/api/auth/login", { email, password });
  const token = extractToken(data);
  if (token) setAccessToken(token);
  cachedMe = null;
  return { data, token };
}

async function refreshSession() {
  const { data } = await api.post("/api/auth/refresh", {});
  const token = extractToken(data);
  if (token) setAccessToken(token);
  cachedMe = null;
  return { data, token };
}

let cachedMe = null;

async function fetchMe({ force = false } = {}) {
  if (cachedMe && !force) return cachedMe;
  const p = api
    .get("/api/auth/me")
    .then(({ data }) => normalizeMe(data))
    .catch((err) => {
      if (cachedMe === p) cachedMe = null;
      throw err;
    });
  cachedMe = p;
  return p;
}

async function logout() {
  clearTokens();
}

function redirectToLogin() {
  try {
    if (typeof window !== "undefined" && window.location) {
      const path = window.location.pathname || "";
      if (path.startsWith("/login")) return;
      // Guest (no token) must never be force-redirected: public data
      // endpoints may 401 when backend requires auth — let the page show
      // ErrorState instead of bouncing guest back to /login in a loop.
      // Only redirect when a (possibly expired) token was present.
      if (!getAccessToken()) return;
      window.location.assign("/login");
    }
  } catch {
    // never throw out of an interceptor
  }
}

function emitUpgradeRequired(detail) {
  try {
    if (typeof window !== "undefined" && typeof window.dispatchEvent === "function") {
      window.dispatchEvent(
        new CustomEvent("onemarket:upgrade-required", { detail: detail ?? null })
      );
    }
  } catch {
    // never throw out of an interceptor
  }
}

// Wire this module's token + handlers into the shared axios instance.
// client.js ships safe defaults (redirect /login on 401, upgrade event on
// 402); calling this keeps the Bearer source live even if storage changes.
function setupAuthInterceptor() {
  setAuthTokenProvider(() => getAccessToken());
  setAuthErrorHandlers({
    onUnauthorized: () => redirectToLogin(),
    onUpgradeRequired: (err) => {
      let detail = null;
      try {
        detail = err?.response?.data ?? null;
      } catch {
        detail = null;
      }
      emitUpgradeRequired(detail);
    },
  });
}

setupAuthInterceptor();

export {
  STORAGE_TOKEN_KEY,
  authHeaderFor,
  clearTokens,
  emitUpgradeRequired,
  extractToken,
  fetchMe,
  getAccessToken,
  login,
  logout,
  normalizeMe,
  redirectToLogin,
  refreshSession,
  register,
  setAccessToken,
  setupAuthInterceptor,
};
