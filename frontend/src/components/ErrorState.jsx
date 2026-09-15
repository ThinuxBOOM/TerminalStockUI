import React from "react";
// Fail-closed UI: there is no "partial failure / cached / stale" state.
// Live data renders; anything else is an explicit error with retry.
// (No StaleBanner exists in the codebase — grep for StaleBanner finds nothing —
// so this component is the single error-with-retry surface.)
import { AlertTriangle, RefreshCw, WifiOff } from "lucide-react";

function ErrorState({
  title = "Something failed",
  detail,
  onRetry,
  icon
}) {
  const Offline = typeof icon === "string" && /off|wifi|network|fetch|unreachable/.test(icon.toLowerCase());
  const Icon = Offline ? WifiOff : AlertTriangle;
  return /* @__PURE__ */ React.createElement("div", { className: "term-panel p-6", role: "alert" },
    /* @__PURE__ */ React.createElement("p", { className: "flex items-center gap-2 text-sm font-bold text-term-red" },
      /* @__PURE__ */ React.createElement(Icon, { className: "h-4 w-4", "aria-hidden": "true" }),
      title),
    detail && /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-xs text-term-muted" }, detail),
    onRetry && /* @__PURE__ */ React.createElement("button", { className: "term-btn-ghost mt-3 inline-flex items-center gap-2 text-xs", type: "button", onClick: onRetry },
      /* @__PURE__ */ React.createElement(RefreshCw, { className: "h-4 w-4", "aria-hidden": "true" }),
      "Retry"));
}
export { ErrorState as default };
