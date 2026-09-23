import { useEffect, useRef, useState } from "react";

/* Count-up helper: animates 0 -> target with rAF when `active` flips true.
 * Runs once (caller gates with a "seen" flag). Instant under
 * prefers-reduced-motion so content stays understandable without animation. */
export function useCountUp(target, active, duration = 1100) {
  const [value, setValue] = useState(0);
  const raf = useRef(0);

  useEffect(() => {
    if (!active) return undefined;
    if (typeof window !== "undefined" && window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setValue(target);
      return undefined;
    }
    const start = performance.now();
    function tick(now) {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      setValue(Math.round(target * eased));
      if (t < 1) raf.current = requestAnimationFrame(tick);
    }
    raf.current = requestAnimationFrame(tick);
    return () => {
      if (raf.current) cancelAnimationFrame(raf.current);
    };
  }, [active, target, duration]);

  return value;
}

export default useCountUp;
