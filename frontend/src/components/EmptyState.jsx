import React from "react";
import { Database, Ghost, Inbox, SearchX } from "lucide-react";

const ICONS = {
  inbox: Inbox,
  empty: Inbox,
  watchlist: Inbox,
  "no-results": SearchX,
  searchx: SearchX,
  noresults: SearchX,
  ghost: Ghost,
  database: Database,
};

function inferIcon(title = "") {
  const t = String(title).toLowerCase();
  if (/no .*pass|no result|not found|no match|nothing match|no data/.test(t)) return SearchX;
  if (/empty|watchlist|nothing here|no .*yet|no backtest|no instrument/.test(t)) return Inbox;
  return Database;
}

function EmptyState({
  title = "Nothing here yet",
  detail,
  actionLabel,
  onAction,
  icon,
  className = ""
}) {
  const key = typeof icon === "string" ? icon.toLowerCase() : null;
  const Icon = (key && ICONS[key]) || (key === "ghost" ? Ghost : null) || inferIcon(title);
  return /* @__PURE__ */ React.createElement("div", { className: `term-panel p-6 text-sm text-term-muted ${className}`, role: "status" },
    /* @__PURE__ */ React.createElement(Icon, { className: "h-8 w-8 text-term-muted/40", "aria-hidden": "true" }),
    /* @__PURE__ */ React.createElement("p", { className: "mt-2 font-bold text-term-text" }, title),
    detail && /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-xs" }, detail),
    actionLabel && onAction && /* @__PURE__ */ React.createElement("button", { className: "term-btn-ghost mt-3 text-xs", type: "button", onClick: onAction }, actionLabel));
}
export { EmptyState as default };
