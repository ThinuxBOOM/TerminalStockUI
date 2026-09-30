import React from "react";
import { Navigate, useLocation } from "react-router-dom";
import Skeleton from "../components/Skeleton";
import { useAuth } from "../hooks/useAuth";

// Route guard for every terminal page. The backend enforces auth on every
// data endpoint; this only avoids rendering pages that cannot load.
function RequireAuth({ children, admin = false }) {
  const { status, isAdmin } = useAuth();
  const location = useLocation();
  if (status === "loading") return <Skeleton label="checking your session…" lines={4} />;
  if (status !== "authenticated") {
    const next = `${location.pathname}${location.search}`;
    return <Navigate to={`/login?next=${encodeURIComponent(next)}`} replace />;
  }
  if (admin && !isAdmin) {
    return (
      <div className="term-panel mx-auto max-w-lg p-4 text-sm text-term-muted" role="alert">
        This page is for administrators only.
      </div>
    );
  }
  return children;
}

export { RequireAuth, RequireAuth as default };
