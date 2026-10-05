// Session handling for the JWT auth API.
//
// - The access token lives in memory only (never localStorage), so an
//   injected script cannot lift a long-lived credential from storage.
// - The refresh token is an httpOnly, SameSite=Strict cookie set by the
//   backend; `refreshSession()` trades it for a new access token. That is
//   how a page reload restores the session.
// - Concurrent 401s share one refresh request (single flight).

import { api, configureAuth } from "./client";

let accessToken = null;
let refreshInFlight = null;
const expiredListeners = new Set();

function getAccessToken() {
  return accessToken;
}

function setAccessToken(token) {
  accessToken = typeof token === "string" && token.trim() !== "" ? token.trim() : null;
  return accessToken;
}

function normalizeUser(data) {
  const d = data ?? {};
  return {
    id: typeof d.id === "string" ? d.id : null,
    email: typeof d.email === "string" ? d.email : null,
    is_admin: d.is_admin === true,
  };
}

function acceptSession(data) {
  setAccessToken(data?.access_token);
  return normalizeUser(data);
}

async function login(email, password) {
  const { data } = await api.post("/api/auth/login", { email, password });
  return acceptSession(data);
}

async function register(email, password) {
  const { data } = await api.post("/api/auth/register", { email, password });
  return acceptSession(data);
}

// Resolves to the new access token, or null when there is no valid session.
function refreshSession() {
  if (!refreshInFlight) {
    refreshInFlight = api
      .post("/api/auth/refresh")
      .then(({ data }) => {
        acceptSession(data);
        return accessToken;
      })
      .catch(() => {
        setAccessToken(null);
        return null;
      })
      .finally(() => {
        refreshInFlight = null;
      });
  }
  return refreshInFlight;
}

async function restoreSession() {
  const token = await refreshSession();
  if (!token) return null;
  const { data } = await api.get("/api/auth/me");
  return normalizeUser(data);
}

async function logout() {
  try {
    if (accessToken) await api.post("/api/auth/logout");
  } finally {
    setAccessToken(null);
  }
}

async function fetchAuthConfig() {
  const { data } = await api.get("/api/auth/config");
  return { registrationOpen: data?.registration_open === true };
}

function onSessionExpired(listener) {
  expiredListeners.add(listener);
  return () => expiredListeners.delete(listener);
}

configureAuth({
  getToken: getAccessToken,
  refresh: refreshSession,
  onSessionExpired: () => {
    setAccessToken(null);
    for (const listener of expiredListeners) listener();
  },
});

export {
  fetchAuthConfig,
  getAccessToken,
  login,
  logout,
  normalizeUser,
  onSessionExpired,
  refreshSession,
  register,
  restoreSession,
  setAccessToken,
};
