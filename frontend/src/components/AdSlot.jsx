// V2 compliant ad slot (Google AdSense, VISIBLE slots only).
//
// Guarantees:
// - Labeled "Advertisement" container with a min-height CLS reserve — the
//   box always occupies space, so lazy fill never shifts layout.
// - Suppressed tiers / VITE_ADS_ENABLED=false return null (DON'T MOUNT,
//   zero ad requests). Premium never renders-then-hides: no opacity:0,
//   display:none, visibility:hidden, 1x1, off-screen, or stacked tricks.
// - IntersectionObserver (rootMargin 200px) pushes the ad only near the
//   viewport; SPA navigations remount a fresh <ins> per pathname.
// - dev (or unconfigured publisher ID) sets data-adtest="on" and performs
//   ZERO network pushes.
// - Any failure degrades to the labeled reserve — no retry loop.
//
// Props: { slotId, format ("leaderboard"|"in-feed"|"in-article"|"auto"),
//   responsive, tier, slotIndex, className }

import React from "react";
import { useLocation } from "react-router-dom";
import {
  AD_IO_ROOT_MARGIN,
  adsClientId,
  adsEnabled,
  isAdTestMode,
  isPushableClientId,
  shouldMountSlot,
  slotMinHeight,
} from "../config/ads";

const AD_PUSH_TIMEOUT_MS = 8000;

// Error boundary: ads must never blank the page. Renders the labeled
// reserve (no ad fill) on crash — exactly once, no retry.
class AdSlotErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { failed: false };
  }
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch(error) {
    try {
      if (typeof console !== "undefined" && console.warn) {
        console.warn("AdSlot suppressed after error (no retry):", error);
      }
    } catch {
      // logging must never throw
    }
  }
  render() {
    if (this.state.failed) {
      const { minHeight, label } = this.props;
      return (
        <div
          role="complementary"
          aria-label={label ?? "Advertisement"}
          style={{ minHeight }}
          className="ad-slot ad-slot--fallback"
        >
          <span className="ad-slot__label">{label ?? "Advertisement"}</span>
        </div>
      );
    }
    return this.props.children;
  }
}

function AdSlotInner({
  slotId,
  format = "auto",
  responsive = true,
  tier = "free",
  slotIndex = 0,
  className = "",
  label = "Advertisement",
}) {
  const location = useLocation();
  const pathname = location?.pathname ?? "/";
  const insRef = React.useRef(null);
  const pushedRef = React.useRef(null);
  const [visible, setVisible] = React.useState(false);
  const [pushFailed, setPushFailed] = React.useState(false);

  const enabled = adsEnabled();
  const allowed = shouldMountSlot(tier, slotIndex);
  const clientId = adsClientId();
  const pushable = isPushableClientId(clientId);
  const adtest = isAdTestMode();
  const minHeight = slotMinHeight(format);

  // Visibility gating: only push when within ~200px of the viewport.
  // No IntersectionObserver (old browsers / SSR) => treat as visible.
  React.useEffect(() => {
    if (!enabled || !allowed) return undefined;
    const el = insRef.current?.parentElement ?? insRef.current;
    if (!el || typeof IntersectionObserver === "undefined") {
      setVisible(true);
      return undefined;
    }
    const io = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setVisible(true);
            io.disconnect();
            break;
          }
        }
      },
      { rootMargin: AD_IO_ROOT_MARGIN }
    );
    io.observe(el);
    return () => io.disconnect();
  }, [enabled, allowed, pathname]);

  // Fresh <ins> per pathname (key below) => push at most once per mount.
  // pushable=false (dev/placeholder) => zero network requests by design.
  React.useEffect(() => {
    pushedRef.current = null;
    setPushFailed(false);
    if (!enabled || !allowed || !visible || !pushable) return undefined;
    let cancelled = false;
    const timer = setTimeout(() => {
      if (cancelled || pushedRef.current) return;
      try {
        const w = typeof window !== "undefined" ? window : null;
        if (!w) return;
        w.adsbygoogle = w.adsbygoogle || [];
        w.adsbygoogle.push({});
        pushedRef.current = true;
      } catch {
        if (!cancelled) setPushFailed(true);
      }
    }, 0);
    const watchdog = setTimeout(() => {
      if (!cancelled && !pushedRef.current) setPushFailed(true);
    }, AD_PUSH_TIMEOUT_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
      clearTimeout(watchdog);
    };
  }, [enabled, allowed, visible, pushable, pathname, slotId]);

  // Suppressed tiers / kill-switch: don't mount anything (zero requests).
  if (!enabled || !allowed) return null;

  const insKey = `${String(slotId ?? "unSlot")}:${pathname}`;

  return (
    <div
      role="complementary"
      aria-label={label}
      style={{ minHeight }}
      className={`ad-slot ad-slot--${format} ${className}`.trim()}
      data-ad-slot={slotId}
      data-ad-format={format}
    >
      <span className="ad-slot__label" aria-hidden="true">
        {label}
      </span>
      {!pushFailed ? (
        <ins
          key={insKey}
          ref={insRef}
          className="adsbygoogle ad-slot__unit"
          style={{ display: "block", minHeight }}
          data-ad-client={clientId || "ca-pub-XXXXXXXXXXXXXXXX"}
          data-ad-slot={slotId}
          data-ad-format={format === "leaderboard" ? "horizontal" : "auto"}
          {...(responsive ? { "data-full-width-responsive": "true" } : {})}
          {...(adtest ? { "data-adtest": "on" } : {})}
        />
      ) : (
        <span className="ad-slot__empty" aria-hidden="true" />
      )}
    </div>
  );
}

function AdSlot(props) {
  const minHeight = slotMinHeight(props.format);
  const label = props.label ?? "Advertisement";
  return (
    <AdSlotErrorBoundary minHeight={minHeight} label={label}>
      <AdSlotInner {...props} />
    </AdSlotErrorBoundary>
  );
}

export {
  AD_PUSH_TIMEOUT_MS,
  AdSlot as default,
  AdSlotErrorBoundary,
  AdSlotInner,
};
