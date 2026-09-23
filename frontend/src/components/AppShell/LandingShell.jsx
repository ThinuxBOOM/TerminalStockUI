import React from "react";

// DEPRECATED thin passthrough — do not add header/footer/max-w here.
// Canonical landing shell is src/features/landing/LandingShell.jsx
// (FloatingNav + Hero + stories + Footer), rendered by
// src/pages/WelcomePage.jsx which owns its own chrome. src/App.jsx renders
// /welcome routes WITHOUT any shell, so wrapping them here would double
// chrome and constrain width. Kept only so any stale import doesn't break.
function LandingShell({ children }) {
  return <>{children}</>;
}

export { LandingShell, LandingShell as default };
