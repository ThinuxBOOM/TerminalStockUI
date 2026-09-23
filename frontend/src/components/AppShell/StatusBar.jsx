import React from "react";
import { useQuery } from "@tanstack/react-query";
import { getHealth } from "../../api/client";
import StatusDot from "../ui/StatusDot";

function formatTime(ms) {
  try {
    const d = new Date(ms);
    if (Number.isNaN(d.getTime())) return "—";
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch {
    return "—";
  }
}

// Compact status strip: API reachability + data freshness + last sync.
// Lightweight: shares the ["health"] cache (staleTime 60s). Never implies
// live when stale — errors / stale cache read Delayed with a timestamp.
function StatusBar() {
  const health = useQuery({
    queryKey: ["health"],
    queryFn: ({ signal }) => getHealth({ signal }),
    retry: false,
    staleTime: 60_000,
  });

  const syncedAt = health.dataUpdatedAt || 0;
  const connected = !health.isError && Boolean(health.data);
  const providers = health.data?.providers ?? [];
  const anyDown = providers.some((p) => {
    const s = String(p.status ?? "").trim().toLowerCase();
    return s === "down" || s === "open" || s === "degraded";
  });
  // Fresh only when connected AND no provider reports degraded/down.
  // Anything else (error, loading, degraded) reads Delayed — never "live".
  const freshness = connected && !anyDown && !health.isStale ? "fresh" : "stale";

  return (
    <footer
      aria-label="Connection status"
      className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-term-border bg-term-panel px-4 py-1.5"
    >
      <StatusDot
        state={connected ? "fresh" : "error"}
        label={connected ? "● API Connected" : health.isLoading ? "○ API Connecting…" : "○ API Unreachable"}
      />
      <StatusDot
        state={freshness === "fresh" ? "fresh" : "warning"}
        label={freshness === "fresh" ? "Data Fresh" : `Data Delayed${syncedAt ? ` · ${formatTime(syncedAt)}` : ""}`}
      />
      <span className="term-num text-2xs text-term-muted">
        Last sync {syncedAt ? formatTime(syncedAt) : "—"}
      </span>
    </footer>
  );
}

export { StatusBar, StatusBar as default };
