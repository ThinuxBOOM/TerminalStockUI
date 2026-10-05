import React, { useCallback, useRef, useState } from "react";

// Shared hover layer: a positioned tooltip inside a relative container.
// show(event, content) / hide(). Content is plain React (rows of label/value).
function useChartTooltip() {
  const ref = useRef(null);
  const [tip, setTip] = useState(null);
  const show = useCallback((e, content) => {
    const box = ref.current?.getBoundingClientRect();
    if (!box) return;
    setTip({ x: e.clientX - box.left, y: e.clientY - box.top, w: box.width, content });
  }, []);
  const hide = useCallback(() => setTip(null), []);
  const node = tip ? (
    <div
      role="tooltip"
      className="pointer-events-none absolute z-20 min-w-[9rem] rounded-md border border-term-border2 bg-term-elevated px-2.5 py-2 text-xs shadow-overlay"
      style={{
        left: Math.min(Math.max(tip.x + 12, 4), Math.max(4, tip.w - 180)),
        top: Math.max(tip.y - 12, 4),
        transform: "translateY(-100%)",
      }}
    >
      {tip.content}
    </div>
  ) : null;
  return { ref, show, hide, node };
}

function TipRow({ label, value, swatch }) {
  return (
    <div className="flex items-center justify-between gap-3 py-0.5">
      <span className="flex items-center gap-1.5 text-term-muted">
        {swatch ? <span aria-hidden="true" className="inline-block h-2 w-2 rounded-sm" style={{ background: swatch }} /> : null}
        {label}
      </span>
      <span className="term-num text-term-text">{value}</span>
    </div>
  );
}

export { TipRow, useChartTooltip };
