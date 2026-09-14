import React, { useMemo } from "react";
function CalibrationChart({ rows, height = 190, title = "Calibration" }) {
  const W = 360;
  const H = height;
  const padL = 30;
  const padR = 10;
  const padT = 10;
  const padB = 22;
  const iw = W - padL - padR;
  const ih = H - padT - padB;
  const x = (p) => padL + Math.min(1, Math.max(0, p)) * iw;
  const y = (p) => padT + (1 - Math.min(1, Math.max(0, p))) * ih;
  const finite = (v) => typeof v === "number" && Number.isFinite(v);
  const safeRows = rows ?? [];
  const plotted = useMemo(
    () => safeRows.filter((r) => r && finite(r.mean_predicted) && finite(r.fraction_positive)),
    [safeRows]
  );
  const maxCount = useMemo(
    () => Math.max(1, ...safeRows.map((r) => typeof r?.count === "number" && Number.isFinite(r.count) ? r.count : 0)),
    [safeRows]
  );
  return /* @__PURE__ */ React.createElement("figure", null, title && /* @__PURE__ */ React.createElement("figcaption", { className: "term-label mb-1" }, title), safeRows.length === 0 ? /* @__PURE__ */ React.createElement("p", { className: "text-xs text-term-muted", role: "status" }, "No calibration bins yet \u2014 run the Backtest Lab.") : /* @__PURE__ */ React.createElement(
    "svg",
    {
      viewBox: `0 0 ${W} ${H}`,
      className: "w-full rounded border border-term-border bg-term-bg",
      role: "img",
      "aria-label": "reliability diagram: predicted vs observed probability"
    },
    /* @__PURE__ */ React.createElement("line", { x1: padL, y1: padT, x2: padL, y2: H - padB, stroke: "#2a3448" }),
    /* @__PURE__ */ React.createElement("line", { x1: padL, y1: H - padB, x2: W - padR, y2: H - padB, stroke: "#2a3448" }),
    /* @__PURE__ */ React.createElement(
      "line",
      {
        x1: x(0),
        y1: y(0),
        x2: x(1),
        y2: y(1),
        stroke: "#3b82a0",
        strokeDasharray: "4 3",
        strokeWidth: 1
      }
    ),
    /* @__PURE__ */ React.createElement("text", { x: W - padR, y: padT + 2, fill: "#5b6b85", fontSize: 9, textAnchor: "end" }, "perfect"),
    [0.25, 0.5, 0.75].map((t) => /* @__PURE__ */ React.createElement("g", { key: t }, /* @__PURE__ */ React.createElement("line", { x1: x(t), y1: padT, x2: x(t), y2: H - padB, stroke: "#1c2433", strokeWidth: 1 }), /* @__PURE__ */ React.createElement("line", { x1: padL, y1: y(t), x2: W - padR, y2: y(t), stroke: "#1c2433", strokeWidth: 1 }), /* @__PURE__ */ React.createElement("text", { x: x(t), y: H - 8, fill: "#5b6b85", fontSize: 9, textAnchor: "middle" }, t.toFixed(2)), /* @__PURE__ */ React.createElement("text", { x: padL - 4, y: y(t) + 3, fill: "#5b6b85", fontSize: 9, textAnchor: "end" }, t.toFixed(2)))),
    safeRows.map((r, i) => {
      if (!r || finite(r.mean_predicted) && finite(r.fraction_positive)) return null;
      if (!finite(r.bin_low) || !finite(r.bin_high)) return null;
      const mid = (r.bin_low + r.bin_high) / 2;
      return /* @__PURE__ */ React.createElement(
        "line",
        {
          key: `e${i}`,
          x1: x(mid),
          y1: H - padB,
          x2: x(mid),
          y2: H - padB + 5,
          stroke: "#ff5c5c",
          strokeWidth: 2
        }
      );
    }),
    plotted.map((r, i) => /* @__PURE__ */ React.createElement(
      "circle",
      {
        key: i,
        cx: x(r.mean_predicted),
        cy: y(r.fraction_positive),
        r: 3 + 7 * Math.sqrt(r.count / maxCount),
        fill: "#3ddc84",
        fillOpacity: 0.75,
        stroke: "#0f141d",
        strokeWidth: 1
      },
      /* @__PURE__ */ React.createElement("title", null, `bin ${finite(r.bin_low) ? r.bin_low.toFixed(2) : "\u2014"}\u2013${finite(r.bin_high) ? r.bin_high.toFixed(2) : "\u2014"} \xB7 n=${r.count} \xB7 pred=${r.mean_predicted.toFixed(3)} \xB7 obs=${r.fraction_positive.toFixed(3)}`)
    )),
    /* @__PURE__ */ React.createElement("text", { x: padL, y: H - 8, fill: "#5b6b85", fontSize: 9 }, "0"),
    /* @__PURE__ */ React.createElement("text", { x: x(1), y: H - 8, fill: "#5b6b85", fontSize: 9, textAnchor: "middle" }, "1 \xB7 predicted \u2192")
  ), /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-[10px] text-term-muted" }, "Dots above the diagonal = under-confident; below = over-confident. Size \u221D bin count. Red ticks = empty bins."));
}
export { CalibrationChart as default };
