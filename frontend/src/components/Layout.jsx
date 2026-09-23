import React from "react";
import AppShell from "./AppShell/AppShell";

// Backwards-compat shim: Layout is now a thin wrapper delegating to AppShell.
// Kept so existing imports keep working; new code should use AppShell (or
// LandingShell for /welcome) directly via src/App.jsx.
function Layout({ children }) {
  return <AppShell>{children}</AppShell>;
}

export { Layout as default };
