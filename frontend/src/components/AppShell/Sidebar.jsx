import React from "react";
import { Link, useLocation } from "react-router-dom";
import {
  Activity,
  BookOpen,
  Crown,
  FileText,
  FlaskConical,
  Globe,
  LayoutDashboard,
  Newspaper,
  ScanSearch,
  SlidersHorizontal,
  Star,
  TrendingUp,
  User,
} from "lucide-react";
import Tooltip from "../ui/Tooltip";

const GROUPS = [
  {
    id: "workspace",
    title: "WORKSPACE",
    items: [
      { to: "/", end: true, label: "Overview", Icon: LayoutDashboard, hint: "Terminal home" },
      { to: "/screener", label: "Markets", Icon: Globe, hint: "Markets overview — top picks" },
      { to: "/watchlist", label: "Watchlist", Icon: Star, hint: "Stocks you follow" },
    ],
  },
  {
    id: "discovery",
    title: "DISCOVERY",
    items: [
      { to: "/search", label: "Discover", Icon: ScanSearch, hint: "Search any company" },
      { to: "/screener", label: "Screener", Icon: SlidersHorizontal, hint: "Best odds right now" },
    ],
  },
  {
    id: "research",
    title: "RESEARCH",
    items: [
      { to: "/security/AAPL", matchPrefix: "/security/", label: "Security Brief", Icon: FileText, hint: "Deep dive on a security" },
      { to: "/forecast/AAPL", matchPrefix: "/forecast/", label: "Forecast", Icon: TrendingUp, hint: "Deterministic outlook" },
      { to: "/backtest", label: "Backtest", Icon: FlaskConical, hint: "How good were past calls?" },
    ],
  },
  {
    id: "data",
    title: "DATA",
    items: [
      { to: "/providers", label: "Data Health", Icon: Activity, hint: "Is the data fresh?" },
      { to: "/#news", matchHash: "#news", label: "News", Icon: Newspaper, hint: "Latest headlines on home" },
      { to: "/account", label: "Account", Icon: User, hint: "Account and session" },
      { to: "/pricing", label: "Pricing", Icon: Crown, hint: "Plans and upgrades" },
      { to: "/welcome", label: "Guide", Icon: BookOpen, hint: "Learn in 2 minutes" },
    ],
  },
];

function isItemActive(item, pathname, hash) {
  if (item.matchHash) return hash === item.matchHash && (pathname === "/" || pathname === "");
  if (item.matchPrefix) return pathname.startsWith(item.matchPrefix);
  if (item.end) return pathname === item.to;
  return pathname === item.to || pathname.startsWith(`${item.to}/`);
}

function SidebarItem({ item, collapsed, pathname, hash, onNavigate }) {
  const active = isItemActive(item, pathname, hash);
  const Icon = item.Icon;
  const link = (
    <Link
      to={item.to}
      onClick={onNavigate}
      aria-current={active ? "page" : undefined}
      title={collapsed ? undefined : (item.hint ?? item.label)}
      className={`relative flex items-center gap-2.5 rounded px-3 py-1.5 font-sans text-xs tracking-wide transition-colors duration-150 ${
        active
          ? "bg-term-greenDim font-semibold text-term-green"
          : "text-term-muted hover:bg-term-panel2 hover:text-term-text"
      } ${collapsed ? "justify-center px-2" : ""}`.trim()}
    >
      {/* Active accent bar (not color alone) + icon state. */}
      <span
        aria-hidden="true"
        className={`absolute left-0 top-1/2 h-5 w-0.5 -translate-y-1/2 rounded-full bg-term-green transition-opacity duration-150 ${
          active ? "opacity-100" : "opacity-0"
        }`.trim()}
      />
      <Icon
        className={`h-4 w-4 shrink-0 ${active ? "text-term-green" : ""}`.trim()}
        aria-hidden="true"
        strokeWidth={active ? 2.5 : 2}
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
            <h2 className="term-label px-2 pb-1">{g.title}</h2>
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
      <div className="mt-auto hidden px-2 py-2 lg:block">
        <button
          type="button"
          onClick={onToggleCollapse}
          aria-expanded={!collapsed}
          aria-controls={id}
          className="term-btn-sm w-full"
        >
          {collapsed ? "EXPAND »" : "« COLLAPSE"}
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
