import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Info } from "lucide-react";
import { deriveMarketState, freshnessOf } from "../api/client";
import { formatDateTime } from "../utils/format";

const FRESHNESS_STATES = ["live", "delayed", "stale", "cached"];

// Accept both canonical provenance keys (delay_minutes / quality_grade /
// fallback_used / missing_fields) and the shorthand aliases described in the
// StatusPill contract (delay / grade / fallback / missing).
function normalizeProvenance(input) {
  if (!input || typeof input !== "object" || Array.isArray(input)) return null;
  const delay =
    typeof input.delay_minutes === "number" && Number.isFinite(input.delay_minutes)
      ? input.delay_minutes
      : typeof input.delay === "number" && Number.isFinite(input.delay)
        ? input.delay
        : -1;
  const missing = Array.isArray(input.missing_fields)
    ? input.missing_fields
    : Array.isArray(input.missing)
      ? input.missing
      : [];
  return {
    source: typeof input.source === "string" && input.source !== "" ? input.source : "unknown",
    as_of: typeof input.as_of === "string" && input.as_of !== "" ? input.as_of : new Date(0).toISOString(),
    delay_minutes: delay,
    quality_grade: input.quality_grade ?? input.grade ?? "U",
    fallback_used:
      typeof input.fallback_used === "boolean" ? input.fallback_used : Boolean(input.fallback),
    missing_fields: missing.map((m) => String(m)),
  };
}

function missingProvenance() {
  return {
    source: "unknown",
    as_of: new Date(0).toISOString(),
    delay_minutes: -1,
    quality_grade: "U",
    fallback_used: true,
    missing_fields: ["market_state"],
  };
}

// XSHG lunch override (11:30-13:00 Asia/Shanghai maps open -> lunch).
// Mirrors MarketStateBadge / isXshgLunchWindow(); `mic` + `now` are optional.
function applyXshgLunch(resolved, mic, now) {
  try {
    const upper = String(mic ?? "").trim().toUpperCase();
    if (upper === "XSHG" && resolved === "open") {
      const at = now instanceof Date ? now : now ? new Date(now) : new Date();
      const fmt = new Intl.DateTimeFormat("en-US", {
        timeZone: "Asia/Shanghai",
        weekday: "short",
        hour: "numeric",
        minute: "numeric",
        second: "numeric",
        hourCycle: "h23",
      });
      const parts = fmt.formatToParts(at);
      const get = (t) => parts.find((part) => part.type === t)?.value;
      const wd = String(get("weekday") ?? "");
      if (wd !== "Sat" && wd !== "Sun") {
        let h = Number(get("hour"));
        if (h === 24) h = 0;
        const mi = Number(get("minute"));
        const se = Number(get("second"));
        if (Number.isFinite(h) && Number.isFinite(mi) && Number.isFinite(se)) {
          const mins = h * 60 + mi + se / 60;
          if (mins >= 11 * 60 + 30 && mins < 13 * 60) return "lunch";
        }
      }
    }
  } catch {
    // never break pill rendering on Intl failures
  }
  return resolved;
}

// Worst-of consolidation. Priority: stale > delayed/cached/closed > live/open.
function statusFor(fresh, market) {
  if (fresh === "stale" || market === "stale") {
    return { label: "STALE", dotColor: "bg-term-red" };
  }
  if (market === "closed") {
    return { label: "CLOSED", dotColor: "bg-term-muted" };
  }
  if (fresh === "cached") {
    return { label: "CACHED", dotColor: "bg-term-amber" };
  }
  if (fresh === "delayed" || market === "delayed") {
    return { label: "DELAYED", dotColor: "bg-term-amber" };
  }
  if (market === "lunch") {
    return { label: "LUNCH", dotColor: "bg-term-amber" };
  }
  return { label: "LIVE", dotColor: "bg-term-green" };
}

function provenanceTitle(prov) {
  if (!prov) return "provenance=missing";
  return (
    `source=${prov.source} as_of=${prov.as_of} delay=${prov.delay_minutes}m ` +
    `grade=${prov.quality_grade} fallback=${prov.fallback_used} ` +
    `missing=[${prov.missing_fields.join(",")}]`
  );
}

function StatusPill({
  freshness,
  marketState,
  state,
  provenance,
  p,
  qualityGrade,
  size = "sm",
  className = "",
  mic,
  now,
}) {
  const [open, setOpen] = useState(false);
  const btnRef = useRef(null);
  const [pos, setPos] = useState({ top: 0, left: 0 });

  // Viewport-aware placement: portal to body (escapes overflow-x-auto table
  // containers that used to clip the box in corners), prefer below the
  // button, flip above when there is no room, and clamp horizontally so the
  // 224px box never runs off the right/left edge.
  const place = () => {
    const el = btnRef.current;
    if (!el || typeof window === "undefined") return;
    const r = el.getBoundingClientRect();
    const W = 224;
    const gap = 6;
    const margin = 8;
    const vw = window.innerWidth || 1024;
    const vh = window.innerHeight || 768;
    const estH = 150;
    const below = vh - r.bottom - gap;
    const top = below >= estH || r.top < estH + gap
      ? r.bottom + gap
      : Math.max(margin, r.top - gap - estH);
    const left = Math.min(
      Math.max(margin, r.left),
      Math.max(margin, vw - W - margin),
    );
    setPos({ top: Math.min(top, Math.max(margin, vh - margin - 40)), left });
  };

  useLayoutEffect(() => {
    if (open) place();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open ]);

  useEffect(() => {
    if (!open) return undefined;
    place();
    function onPointer(e) {
      if (btnRef.current && !btnRef.current.contains(e.target)) {
        const dlg = document.getElementById("statuspill-dialog");
        if (dlg && !dlg.contains(e.target)) setOpen(false);
      }
    }
    function onKey(e) {
      if (e.key === "Escape") {
        setOpen(false);
        btnRef.current?.focus();
      }
    }
    window.addEventListener("pointerdown", onPointer, { passive: true });
    window.addEventListener("keydown", onKey);
    window.addEventListener("scroll", place, { passive: true, capture: true });
    window.addEventListener("resize", place);
    return () => {
      window.removeEventListener("pointerdown", onPointer);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", place, { capture: true });
      window.removeEventListener("resize", place);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open ]);

  const provInput =
    provenance ??
    p ??
    (freshness && typeof freshness === "object" ? freshness : null) ??
    null;
  const prov = normalizeProvenance(provInput);

  let fresh = null;
  if (typeof freshness === "string") {
    const s = freshness.trim().toLowerCase();
    if (FRESHNESS_STATES.includes(s)) fresh = s;
  }
  if (!fresh) {
    try {
      fresh = prov ? freshnessOf(prov) : "stale";
    } catch {
      fresh = "stale";
    }
  }

  const explicit =
    typeof marketState === "string" && marketState.trim() !== ""
      ? marketState
      : typeof state === "string" && state.trim() !== ""
        ? state
        : null;
  let resolved = "delayed";
  try {
    resolved = deriveMarketState(prov ?? missingProvenance(), explicit);
  } catch {
    resolved = "delayed";
  }
  resolved = applyXshgLunch(resolved, mic, now);

  const { label, dotColor } = statusFor(fresh, resolved);
  const grade = qualityGrade ?? prov?.quality_grade ?? null;
  const title = provenanceTitle(prov);
  const missing = prov?.missing_fields ?? [];

  return (
    <span
      className={`inline-flex items-center gap-1.5 text-2xs font-semibold tracking-wide tabular-nums text-term-muted ${className}`}
      title={title}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${dotColor}`} aria-hidden="true" />
      <span>{label}</span>
      {size === "lg" && grade ? (
        <sup
          className="text-[9px] font-bold leading-none text-term-cyan"
          title={`data quality grade ${grade}`}
        >
          {grade}
        </sup>
      ) : null}
      <span className="relative inline-flex items-center">
        <button
          ref={btnRef}
          type="button"
          aria-label="Data provenance details"
          aria-expanded={open}
          onClick={() => setOpen((o) => !o)}
          className="rounded p-0.5 text-term-muted hover:text-term-text"
        >
          <Info className="h-3 w-3" aria-hidden="true" />
        </button>
        {open && typeof document !== "undefined"
          ? createPortal(
              <div
                id="statuspill-dialog"
                role="dialog"
                aria-label="Data provenance details"
                style={{ top: pos.top, left: pos.left }}
                className="term-panel-nested fixed z-50 mt-0 w-56 max-w-[calc(100vw-16px)] break-words p-2 text-left text-[10px] font-normal normal-case leading-relaxed tracking-normal text-term-muted shadow-panel-lg"
              >
                <div className="break-all">
                  src: <b className="text-term-text">{prov ? prov.source : "unavailable"}</b>
                </div>
                <div className="break-all">as_of: {prov ? formatDateTime(prov.as_of) : "—"}</div>
                <div>delay: {prov ? `${prov.delay_minutes}m` : "—"}</div>
                <div>
                  Q: <b className="text-term-cyan">{grade ?? "U"}</b>
                </div>
                {prov?.fallback_used ? <div className="text-term-amber">fallback</div> : null}
                {missing.length > 0 ? (
                  <div className="break-all text-term-red">missing: {missing.join(", ")}</div>
                ) : null}
              </div>,
              document.body,
            )
          : null}
      </span>
    </span>
  );
}

export { StatusPill, StatusPill as default };
