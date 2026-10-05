import React, { createContext, useCallback, useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import * as authApi from "../api/auth";

// status: "loading" (restoring the session on first load) | "authenticated" | "anonymous"
const AuthContext = createContext(null);

function AuthProvider({ children }) {
  const queryClient = useQueryClient();
  const [state, setState] = useState({ status: "loading", user: null });

  useEffect(() => {
    let active = true;
    authApi
      .restoreSession()
      .then((user) => {
        if (active) setState(user ? { status: "authenticated", user } : { status: "anonymous", user: null });
      })
      .catch(() => {
        if (active) setState({ status: "anonymous", user: null });
      });
    const unsubscribe = authApi.onSessionExpired(() => {
      queryClient.clear();
      setState({ status: "anonymous", user: null });
    });
    return () => {
      active = false;
      unsubscribe();
    };
  }, [queryClient]);

  const login = useCallback(async (email, password) => {
    const user = await authApi.login(email, password);
    setState({ status: "authenticated", user });
    return user;
  }, []);

  const register = useCallback(async (email, password) => {
    const user = await authApi.register(email, password);
    setState({ status: "authenticated", user });
    return user;
  }, []);

  const logout = useCallback(async () => {
    try {
      await authApi.logout();
    } finally {
      queryClient.clear();
      setState({ status: "anonymous", user: null });
    }
  }, [queryClient]);

  const value = useMemo(
    () => ({
      status: state.status,
      user: state.user,
      isAuthenticated: state.status === "authenticated",
      isAdmin: state.user?.is_admin === true,
      login,
      register,
      logout,
    }),
    [state, login, register, logout]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export { AuthContext, AuthProvider };
