// V2 auth state hook (gradual replacement for useCurrentUserStub).
// Guest path (no token) resolves synchronously from the stub with ZERO
// network requests. Authed path (token present) loads GET /api/auth/me once
// (module-cached) and exposes login/register/logout. Backend enforces tiers;
// this hook only mirrors them for UX.

import { useCallback, useEffect, useRef, useState } from "react";
import {
  clearTokens,
  fetchMe,
  getAccessToken,
  login as apiLogin,
  logout as apiLogout,
  register as apiRegister,
} from "../api/auth";
import { getCurrentUserStub } from "../api/authStub";

function guestSnapshot() {
  const stub = getCurrentUserStub();
  return {
    userId: stub.userId,
    email: null,
    tier: stub.tier ?? "Free",
    is_admin: false,
    isGuest: true,
    isAuthenticated: false,
  };
}

function useAuth() {
  const [snapshot, setSnapshot] = useState(() => ({
    ...guestSnapshot(),
    loading: getAccessToken() !== null,
    error: null,
  }));
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const refresh = useCallback(async ({ force = false } = {}) => {
    const token = getAccessToken();
    if (!token) {
      if (mountedRef.current) {
        setSnapshot({ ...guestSnapshot(), loading: false, error: null });
      }
      return guestSnapshot();
    }
    if (mountedRef.current) {
      setSnapshot((s) => ({ ...s, loading: true, error: null }));
    }
    try {
      const me = await fetchMe({ force });
      const next = {
        userId: me.id,
        email: me.email,
        tier: me.tier ?? "Free",
        is_admin: me.is_admin === true,
        isGuest: false,
        isAuthenticated: true,
      };
      if (mountedRef.current) setSnapshot({ ...next, loading: false, error: null });
      return next;
    } catch (err) {
      const status = err?.response?.status;
      // Expired/revoked token: drop it so the UI falls back to guest
      // instead of retrying authed calls forever (the 401 interceptor
      // already routed to /login).
      if (status === 401) clearTokens();
      if (mountedRef.current) {
        setSnapshot({
          ...guestSnapshot(),
          loading: false,
          error: err instanceof Error ? err.message : "auth refresh failed",
        });
      }
      return guestSnapshot();
    }
  }, []);

  useEffect(() => {
    if (getAccessToken()) void refresh();
  }, [refresh]);

  const login = useCallback(
    async (email, password) => {
      const res = await apiLogin(email, password);
      await refresh({ force: true });
      return res;
    },
    [refresh]
  );

  const register = useCallback(
    async (email, password) => {
      const res = await apiRegister(email, password);
      await refresh({ force: true });
      return res;
    },
    [refresh]
  );

  const logout = useCallback(async () => {
    await apiLogout();
    if (mountedRef.current) {
      setSnapshot({ ...guestSnapshot(), loading: false, error: null });
    }
  }, []);

  return { ...snapshot, login, register, logout, refresh };
}

export { guestSnapshot, useAuth };
export default useAuth;
