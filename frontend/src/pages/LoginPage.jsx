// V2 real login/register page (replaces the /login stub route).
// JWT via POST /api/auth/register + /api/auth/login; refresh cookie handled
// server-side. Guest browsing still works everywhere — login only unlocks
// tiered features + billing.

import React, { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";

function friendlyAuthError(err) {
  const status = err?.response?.status;
  try {
    const data = err?.response?.data;
    const detail =
      (typeof data?.detail === "string" && data.detail) ||
      (typeof data?.message === "string" && data.message) ||
      null;
    if (detail) return detail.slice(0, 300);
  } catch {
    // fall through
  }
  if (status === 401) return "Wrong email or password. Try again.";
  if (status === 409) return "That email already has an account — sign in instead.";
  if (status === 422) return "Check the form: valid email + password of at least 10 characters.";
  if (err instanceof Error && err.message) return err.message;
  return "Sign-in failed. Retry — the terminal works fine as guest meanwhile.";
}

function LoginPage() {
  const { login, register } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [mode, setMode] = useState(() => {
    try {
      const params = new URLSearchParams(location.search || "");
      return params.get("mode") === "register" ? "register" : "login";
    } catch {
      return "login";
    }
  });
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const next = (() => {
    try {
      const params = new URLSearchParams(location.search || "");
      const n = params.get("next");
      return n && n.startsWith("/") ? n : "/account";
    } catch {
      return "/account";
    }
  })();

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

  return (
    <div className="mx-auto max-w-md">
      <p className="text-[11px] tracking-widest text-term-muted">
        {mode === "register" ? "CREATE ACCOUNT" : "SIGN IN"}
      </p>
      <h1 className="mt-1 text-xl font-bold text-term-text">
        {mode === "register" ? "Join OneMarket" : "Welcome back"}
      </h1>
      <p className="mt-1 text-sm text-term-muted">
        Free to start. Paid plans unlock deeper research — see{" "}
        <Link to="/pricing" className="text-term-green hover:underline">
          pricing
        </Link>
        .
      </p>

      <form onSubmit={onSubmit} className="term-panel mt-4 space-y-3 p-4">
        <div>
          <label className="term-label" htmlFor="login-email">
            Email
          </label>
          <input
            id="login-email"
            className="term-input mt-1 w-full"
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@example.com"
            spellCheck={false}
          />
        </div>
        <div>
          <label className="term-label" htmlFor="login-password">
            Password
            {mode === "register" ? " (min 10 characters)" : ""}
          </label>
          <input
            id="login-password"
            className="term-input mt-1 w-full"
            type="password"
            required
            minLength={mode === "register" ? 10 : 1}
            autoComplete={mode === "register" ? "new-password" : "current-password"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••••"
          />
        </div>
        {error ? (
          <p className="text-xs text-term-red" role="alert">
            {error}
          </p>
        ) : null}
        <button className="term-btn w-full text-sm" type="submit" disabled={busy}>
          {busy ? "PLEASE WAIT…" : mode === "register" ? "CREATE FREE ACCOUNT →" : "SIGN IN →"}
        </button>
        <button
          type="button"
          className="term-btn-ghost w-full text-xs"
          onClick={() => {
            setMode(mode === "register" ? "login" : "register");
            setError(null);
          }}
        >
          {mode === "register" ? "Have an account? Sign in" : "New here? Create a free account"}
        </button>
      </form>

      <div className="mt-4 flex flex-wrap gap-2 text-xs">
        <Link to="/pricing" className="term-btn-ghost text-xs">
          PRICING →
        </Link>
        <Link to="/" className="term-btn-ghost text-xs">
          CONTINUE AS GUEST →
        </Link>
      </div>
    </div>
  );
}

export { LoginPage as default };
