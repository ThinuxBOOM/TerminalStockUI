import { useEffect, useRef, useState } from "react";

/* Window scroll progress 0..1, throttled with rAF so we never spam setState
 * on every scroll event. Reason: drives only the thin nav progress bar. */
export function useScrollProgress() {
  const [progress, setProgress] = useState(0);
  const raf = useRef(0);

  useEffect(() => {
    function update() {
      raf.current = 0;
      const el = document.documentElement;
      const max = el.scrollHeight - el.clientHeight;
      setProgress(max > 0 ? Math.min(1, Math.max(0, el.scrollTop / max)) : 0);
    }
    function onScroll() {
      if (!raf.current) raf.current = requestAnimationFrame(update);
    }
    update();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      if (raf.current) cancelAnimationFrame(raf.current);
    };
  }, []);

  return progress;
}

export default useScrollProgress;
