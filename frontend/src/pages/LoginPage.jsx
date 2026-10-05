import "../features/landing/landing.css";
import React, { useEffect, useState } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { fetchAuthConfig } from "../api/auth";
import { useAuth } from "../hooks/useAuth";

function friendlyAuthError(err) {
  const status = err?.response?.status;
  const detail = err?.response?.data?.detail;
  if (status === 401) return "Wrong email or password.";
  if (status === 403) return "Sign-up is closed on this server. Ask the administrator for an account.";
  if (status === 409) return "That email already has an account. Sign in instead.";
  if (status === 429) return "Too many attempts. Wait a minute and try again.";
  if (typeof detail === "string" && detail) return detail.slice(0, 300);
  if (!err?.response) return "Could not reach the server. Check your connection and retry.";
  return "Sign-in failed. Please retry.";
}

// Only same-app paths are allowed as a post-login destination (no open redirect).
function safeNext(search) {
  try {
    const next = new URLSearchParams(search || "").get("next") || "";
    return next.startsWith("/") && !next.startsWith("//") ? next : "/app";
  } catch {
    return "/app";
  }
}

function LoginPage() {
  const { status, login, register } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const next = safeNext(location.search);
  const config = useQuery({ queryKey: ["auth-config"], queryFn: fetchAuthConfig, staleTime: 300_000 });
  const registrationOpen = config.data?.registrationOpen === true;
  const [mode, setMode] = useState("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!registrationOpen && mode === "register") setMode("login");
  }, [registrationOpen, mode]);

  if (status === "authenticated") return <Navigate to={next} replace />;

  async function onSubmit(e) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      if (mode === "register") await register(email.trim(), password);
      else await login(email.trim(), password);
      navigate(next, { replace: true });
    } catch (err) {
      setError(friendlyAuthError(err));
    } finally {
      setBusy(false);
    }
  }

  const registering = mode === "register";
  return (
    <div className="lp relative flex min-h-screen items-center justify-center px-4">
      <div className="lp-grid" />
      <div className="relative w-full max-w-sm">
        <Link to="/" className="flex items-center justify-center gap-2.5" aria-label="OneMarket home">
          <img src="/logo.svg" alt="" className="h-9 w-9" width="36" height="36" />
          <span className="text-lg font-semibold tracking-tight text-white">OneMarket</span>
        </Link>
        <form onSubmit={onSubmit} className="lp-glass mt-8 space-y-4 rounded-2xl p-6">
          <div>
            <h1 className="text-lg font-semibold text-white">{registering ? "Create your account" : "Welcome back"}</h1>
            <p className="mt-1 text-sm text-term-muted">{registering ? "Use at least 10 characters for your password." : "Sign in to the terminal."}</p>
          </div>
          <div>
            <label className="text-xs font-medium text-term-muted" htmlFor="login-email">Email</label>
            <input
              id="login-email"
              className="term-input mt-1.5 w-full"
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              spellCheck={false}
            />
          </div>
          <div>
            <label className="text-xs font-medium text-term-muted" htmlFor="login-password">Password</label>
            <input
              id="login-password"
              className="term-input mt-1.5 w-full"
              type="password"
              required
              minLength={registering ? 10 : 1}
              maxLength={72}
              autoComplete={registering ? "new-password" : "current-password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          {error ? (
            <p className="rounded-md border border-term-red/30 bg-term-redDim px-3 py-2 text-xs text-term-red" role="alert">{error}</p>
          ) : null}
          <button className="term-btn w-full py-2.5" type="submit" disabled={busy || status === "loading"}>
            {busy ? "Please wait…" : registering ? "Create account" : "Sign in"}
          </button>
          {registrationOpen ? (
            <button
              type="button"
              className="w-full text-center text-xs text-term-muted hover:text-term-text"
              onClick={() => {
                setMode(registering ? "login" : "register");
                setError(null);
              }}
            >
              {registering ? "Have an account? Sign in" : "New here? Create an account"}
            </button>
          ) : (
            <p className="text-center text-xs text-term-faint">Accounts are created by your administrator.</p>
          )}
        </form>
        <p className="mt-6 text-center text-2xs text-term-faint">Statistical estimates, not investment advice.</p>
      </div>
    </div>
  );
}

export { LoginPage as default, safeNext };
