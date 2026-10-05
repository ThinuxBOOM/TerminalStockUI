import React from "react";
import { useParams } from "react-router-dom";
import SecurityPage from "../features/security/SecurityPage";

function safeDecode(v) {
  try {
    return decodeURIComponent(v);
  } catch {
    return v;
  }
}

function SecurityBriefPage() {
  const { symbol = "AAPL" } = useParams();
  const decoded = safeDecode(symbol).toUpperCase();
  // Remount per symbol so tab-local state (horizon, sizer inputs) resets.
  return <SecurityPage key={decoded} symbol={decoded} />;
}

export { SecurityBriefPage as default };
