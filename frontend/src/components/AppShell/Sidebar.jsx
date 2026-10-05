import React from "react";
import { Link, useLocation } from "react-router-dom";
import {
  Activity,
  ArrowLeft,
  BriefcaseBusiness,
  FlaskConical,
  LayoutDashboard,
  LineChart,
  ScanSearch,
  SlidersHorizontal,
  Star,
  User,
} from "lucide-react";
import Tooltip from "../ui/Tooltip";

function lastSecurity() {
  try {
    const sym = String(localStorage.getItem("onemarket.lastSecurity.v1") ?? "");
    return /^[\w.^-]{1,20}$/.test(sym) ? sym : "AAPL";
  } catch {
    return "AAPL";
  }
}

const GROUPS = [
  {
    id: "workspace",
    title: "Workspace",
    items: [
      { to: "/app", end: true, label: "Overview", Icon: LayoutDashboard, hint: "Markets, signals and your watchlist" },
      { to: "/watchlist", label: "Watchlist", Icon: Star, hint: "Stocks you follow" },
      { to: "/portfolio", label: "Portfolio risk", Icon: BriefcaseBusiness, hint: "Volatility, VaR and correlations of your holdings" },
    ],
  },
  {
    id: "research",
    title: "Research",
    items: [
      { to: "/screener", label: "Screener", Icon: SlidersHorizontal, hint: "Rank every stock by the model" },
      { to: "/search", label: "Search", Icon: ScanSearch, hint: "Find any stock" },
      { to: "/security/", dynamic: lastSecurity, matchPrefix: "/security/", label: "Security", Icon: LineChart, hint: "Chart, forecast, risk and fundamentals" },
      { to: "/model", label: "Model Lab", Icon: FlaskConical, hint: "How accurate the forecasts are" },
    ],
  },
  {
    id: "system",
    title: "System",
    items: [
      { to: "/providers", label: "Data health", Icon: Activity, hint: "Is the data fresh?" },
      { to: "/account", label: "Account", Icon: User, hint: "Account and session" },
    ],
  },
];

function isItemActive(item, pathname, hash) {
  if (item.matchHash) return hash === item.matchHash && (pathname === "/app" || pathname === "");
  if (item.matchPrefix) return pathname.startsWith(item.matchPrefix);
  if (item.end) return pathname === item.to;
  return pathname === item.to || pathname.startsWith(`${item.to}/`);
}

function SidebarItem({ item, collapsed, pathname, hash, onNavigate }) {
  const active = isItemActive(item, pathname, hash);
  const Icon = item.Icon;
  const to = item.dynamic ? `${item.to}${encodeURIComponent(item.dynamic())}` : item.to;
  const link = (
    <Link
      to={to}
      onClick={onNavigate}
      aria-current={active ? "page" : undefined}
      title={collapsed ? undefined : (item.hint ?? item.label)}
      className={`relative flex items-center gap-2.5 rounded-md px-3 py-1.5 font-sans text-[13px] transition-colors duration-150 ${
        active
          ? "bg-term-accentDim font-medium text-term-text"
          : "text-term-muted hover:bg-term-panel2 hover:text-term-text"
      } ${collapsed ? "justify-center px-2" : ""}`.trim()}
    >
      {/* Active accent bar (not color alone) + icon state. */}
      <span
        aria-hidden="true"
        className={`absolute left-0 top-1/2 h-5 w-0.5 -translate-y-1/2 rounded-full bg-term-accent transition-opacity duration-150 ${
          active ? "opacity-100" : "opacity-0"
        }`.trim()}
      />
      <Icon
        className={`h-4 w-4 shrink-0 ${active ? "text-term-accent" : ""}`.trim()}
        aria-hidden="true"
        strokeWidth={active ? 2.25 : 1.75}
      />
      {collapsed ? <span className="sr-only">{item.label}</span> : <span className="truncate">{item.label}</span>}
    </Link>
  );
  if (collapsed) {
    return (
      <Tooltip label={item.label} side="right">
        {link}
      </Tooltip>
    );
  }
  return link;
}

function Sidebar({ collapsed = false, mobileOpen = false, onCloseMobile, onToggleCollapse, id = "app-sidebar" }) {
  const location = useLocation();
  const pathname = location.pathname ?? "/";
  const hash = location.hash ?? "";

  // Esc closes the mobile drawer; focus stays in the document flow.
  React.useEffect(() => {
    if (!mobileOpen) return undefined;
    function onKey(e) {
      if (e.key === "Escape" && onCloseMobile) {
        e.preventDefault();
        onCloseMobile();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mobileOpen, onCloseMobile]);

  const groups = (
    <div className="flex h-full flex-col">
      {GROUPS.map((g) => (
        <div key={g.id} className="px-2 py-2">
          {collapsed ? (
            <div aria-hidden="true" className="mx-2 mb-1 border-t border-term-border" title={g.title} />
          ) : (
            <h2 className="px-3 pb-1 text-2xs font-medium uppercase tracking-[0.08em] text-term-faint">{g.title}</h2>
          )}
          <nav aria-label={g.title} className="space-y-0.5">
            {g.items.map((item) => (
              <SidebarItem
                key={`${g.id}:${item.label}`}
                item={item}
                collapsed={collapsed}
                pathname={pathname}
                hash={hash}
                onNavigate={onCloseMobile}
              />
            ))}
          </nav>
        </div>
      ))}
      <div className="mt-auto px-2 py-2">
        <Link
          to="/"
          onClick={onCloseMobile}
          aria-label="Back to landing page"
          title="Back to landing page"
          className="term-btn-sm flex w-full items-center justify-center gap-1.5 whitespace-nowrap"
        >
          <ArrowLeft className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          {collapsed ? (
            <span className="sr-only">Back to landing page</span>
          ) : (
            <span>Home page</span>
          )}
        </Link>
        <button
          type="button"
          onClick={onToggleCollapse}
          aria-expanded={!collapsed}
          aria-controls={id}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          className="term-btn-sm mt-2 hidden w-full whitespace-nowrap lg:block"
        >
          {collapsed ? "»" : "« Collapse"}
        </button>
      </div>
    </div>
  );

  return (
    <>
      {/* Desktop rail */}
      <aside
        id={id}
        aria-label="Terminal navigation"
        className={`sticky top-0 hidden h-screen shrink-0 overflow-y-auto border-r border-term-border bg-term-panel transition-[width,opacity] duration-150 lg:block ${
          collapsed ? "w-14" : "w-56"
        }`.trim()}
      >
        {groups}
      </aside>

      {/* Mobile drawer / overlay */}
      {mobileOpen ? (
        <div className="fixed inset-0 z-50 lg:hidden" role="dialog" aria-modal="true" aria-label="Terminal navigation">
          <div
            className="absolute inset-0 bg-black/60"
            aria-hidden="true"
            onClick={onCloseMobile}
          />
          <aside
            id="app-sidebar-mobile"
            aria-label="Terminal navigation"
            className="absolute left-0 top-0 h-full w-64 overflow-y-auto border-r border-term-border2 bg-term-panel shadow-overlay"
          >
            <div className="flex justify-end p-2 lg:hidden">
              <button
                type="button"
                onClick={onCloseMobile}
                aria-label="Close navigation"
                className="term-btn-sm"
              >
                ✕ CLOSE
              </button>
            </div>
            {groups}
          </aside>
        </div>
      ) : null}
    </>
  );
}

export { GROUPS, Sidebar, Sidebar as default };
