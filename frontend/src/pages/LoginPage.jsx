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
    <div className="flex min-h-screen items-center justify-center bg-term-bg px-4">
      <div className="w-full max-w-md">
        <Link to="/" className="text-[11px] tracking-widest text-term-muted hover:text-term-text">
          ← ONEMARKET ANALYZER
        </Link>
        <h1 className="mt-2 text-xl font-bold text-term-text">
          {registering ? "Create an account" : "Sign in"}
        </h1>
        <form onSubmit={onSubmit} className="term-panel mt-4 space-y-3 p-4">
          <div>
            <label className="term-label" htmlFor="login-email">Email</label>
            <input
              id="login-email"
              className="term-input mt-1 w-full"
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              spellCheck={false}
            />
          </div>
          <div>
            <label className="term-label" htmlFor="login-password">
              Password{registering ? " (at least 10 characters)" : ""}
            </label>
            <input
              id="login-password"
              className="term-input mt-1 w-full"
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
            <p className="text-xs text-term-red" role="alert">{error}</p>
          ) : null}
          <button className="term-btn w-full text-sm" type="submit" disabled={busy || status === "loading"}>
            {busy ? "PLEASE WAIT…" : registering ? "CREATE ACCOUNT →" : "SIGN IN →"}
          </button>
          {registrationOpen ? (
            <button
              type="button"
              className="term-btn-ghost w-full text-xs"
              onClick={() => {
                setMode(registering ? "login" : "register");
                setError(null);
              }}
            >
              {registering ? "Have an account? Sign in" : "New here? Create an account"}
            </button>
          ) : null}
        </form>
        <p className="mt-3 text-2xs text-term-muted">
          Forecasts are experimental probabilities, not investment advice.
        </p>
      </div>
    </div>
  );
}

export { LoginPage as default, safeNext };
