import React, { useEffect, useRef, useState } from "react";

/* IntersectionObserver reveal helper. Reason for animation: progression —
 * content fades/slides in once as it enters, guiding the eye down the story.
 * Uses transform/opacity only; never replays aggressively (once=true default).
 * Content is fully readable if JS/IO is unavailable (visible after timeout
 * fallback is NOT needed because we only hide via JS-added class). */
function Reveal({ as: Tag = "div", children, className = "", delay = 0, once = true, id, ...rest }) {
  const ref = useRef(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    if (typeof IntersectionObserver === "undefined") {
      setVisible(true);
      return undefined;
    }
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => {
          if (e.isIntersecting) {
            setVisible(true);
            if (once) io.disconnect();
          } else if (!once) {
            setVisible(false);
          }
        });
      },
      { threshold: 0.15, rootMargin: "0px 0px -8% 0px" }
    );
    io.observe(el);
    return () => io.disconnect();
  }, [once]);

  return (
    <Tag
      ref={ref}
      id={id}
      className={`lp-reveal${visible ? " is-visible" : ""}${className ? ` ${className}` : ""}`}
      style={{ "--lp-delay": `${delay}ms` }}
      {...rest}
    >
      {children}
    </Tag>
  );
}

export { Reveal as default };
