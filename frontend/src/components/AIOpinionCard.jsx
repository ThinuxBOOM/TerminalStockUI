import React from "react";
import ProvenanceBadge from "./ProvenanceBadge";
const DISAGREE_TOL = 0.15;
function AIOpinionCard({
  opinion,
  deterministicProbability,
  deterministicDirection,
  provenance,
  onRequest,
  requesting,
  requestError
}) {
  if (!opinion) {
    return /* @__PURE__ */ React.createElement("section", { className: "term-panel p-4" }, /* @__PURE__ */ React.createElement("p", { className: "term-label" }, "AI opinion \xB7 bounded (capped 20%)"), /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-sm text-term-muted" }, "No AI opinion attached. AI runs only on explicit request and can at most nudge \u2014 never override \u2014 the deterministic forecast."), onRequest && /* @__PURE__ */ React.createElement("button", { className: "term-btn mt-3", type: "button", disabled: requesting, onClick: onRequest }, requesting ? "REQUESTING\u2026" : "REQUEST AI OPINION"), requestError && /* @__PURE__ */ React.createElement("p", { className: "mt-2 text-xs text-term-red" }, requestError));
  }
  const prob = typeof opinion.probability === "number" && Number.isFinite(opinion.probability) ? opinion.probability : null;
  const gap = typeof deterministicProbability === "number" && Number.isFinite(deterministicProbability) && prob !== null ? Math.abs(prob - deterministicProbability) : null;
  const opinionDir = String(opinion.direction ?? "").toLowerCase();
  const detDir = String(deterministicDirection ?? "").toLowerCase();
  const dirMismatch = detDir !== "" && opinionDir !== "" && opinionDir !== detDir;
  const disagree = gap !== null && gap > DISAGREE_TOL || dirMismatch;
  const evidence = opinion.evidence_ids ?? [];
  const catalysts = opinion.catalysts ?? [];
  const risks = opinion.risks ?? [];
  const limitations = opinion.limitations ?? [];
  const noEvidence = evidence.length === 0;
  const prov = opinion.provenance ?? provenance ?? null;
  return /* @__PURE__ */ React.createElement("section", { className: "term-panel p-4" }, /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-center gap-2" }, /* @__PURE__ */ React.createElement("p", { className: "term-label" }, "AI opinion \xB7 bounded"), /* @__PURE__ */ React.createElement(
    "span",
    {
      className: "rounded border border-term-amber px-2 py-0.5 text-[10px] font-bold tracking-widest text-term-amber",
      title: "Fixed v1 policy: AI weight capped at 20%, not user-adjustable"
    },
    "CAPPED 20%"
  ), opinion.stub === true && /* @__PURE__ */ React.createElement(
    "span",
    {
      className: "rounded border border-term-red px-2 py-0.5 text-[10px] font-bold tracking-widest text-term-red",
      title: limitations[0] ?? "No live model call was made"
    },
    "STUB \u2014 NO LIVE CALL"
  ), opinion.provider && /* @__PURE__ */ React.createElement("span", { className: "text-[11px] text-term-muted" }, opinion.provider, opinion.model ? ` \xB7 ${opinion.model}` : "")), /* @__PURE__ */ React.createElement("p", { className: "mt-2 text-sm" }, "Direction: ", /* @__PURE__ */ React.createElement("b", { className: "text-term-text" }, opinion.direction ?? "\u2014"), " \xB7 horizon", " ", /* @__PURE__ */ React.createElement("b", { className: "text-term-text" }, opinion.time_horizon_days, "d"), prov && /* @__PURE__ */ React.createElement("span", { className: "ml-2" }, /* @__PURE__ */ React.createElement(ProvenanceBadge, { p: prov }))), /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-2xl font-bold" }, prob !== null ? `${(prob * 100).toFixed(1)}%` : "unavailable", /* @__PURE__ */ React.createElement("span", { className: "ml-2 align-middle text-[10px] font-normal text-term-muted" }, "AI-only figure \u2014 blended forecast moves at most 20% of the way toward it")), disagree && /* @__PURE__ */ React.createElement(
    "div",
    {
      className: "mt-2 rounded border border-term-amber bg-term-panel p-2 text-xs text-term-amber",
      role: "alert"
    },
    "\u26A0 Disagreement with deterministic core",
    gap !== null ? ` (\u0394 ${(gap * 100).toFixed(1)}pp)` : "",
    dirMismatch ? " \xB7 direction mismatch" : "",
    " \u2014 treat blended confidence as lower."
  ), noEvidence && /* @__PURE__ */ React.createElement("div", { className: "mt-2 rounded border border-term-red p-2 text-xs text-term-red", role: "alert" }, "\u2715 Rejected-grade opinion: no evidence_ids supplied. Per policy, claims without evidence IDs are discarded \u2014 this card is shown for transparency only."), /* @__PURE__ */ React.createElement("div", { className: "mt-2 grid gap-2 text-xs md:grid-cols-2" }, /* @__PURE__ */ React.createElement("div", { className: "rounded border border-term-border p-2" }, /* @__PURE__ */ React.createElement("p", { className: "font-bold text-term-green" }, "Catalysts"), catalysts.length === 0 ? /* @__PURE__ */ React.createElement("p", { className: "text-term-muted" }, "\u2014") : /* @__PURE__ */ React.createElement("ul", { className: "list-disc pl-4 text-term-muted" }, catalysts.map((c, i) => /* @__PURE__ */ React.createElement("li", { key: `${c}-${i}` }, c)))), /* @__PURE__ */ React.createElement("div", { className: "rounded border border-term-border p-2" }, /* @__PURE__ */ React.createElement("p", { className: "font-bold text-term-red" }, "Risks"), risks.length === 0 ? /* @__PURE__ */ React.createElement("p", { className: "text-term-muted" }, "\u2014") : /* @__PURE__ */ React.createElement("ul", { className: "list-disc pl-4 text-term-muted" }, risks.map((c, i) => /* @__PURE__ */ React.createElement("li", { key: `${c}-${i}` }, c))))), /* @__PURE__ */ React.createElement("div", { className: "mt-2 text-xs" }, /* @__PURE__ */ React.createElement("p", { className: "text-term-muted" }, "Evidence:", " ", evidence.length === 0 ? /* @__PURE__ */ React.createElement("span", { className: "text-term-red" }, "none supplied") : evidence.map((e, i) => /* @__PURE__ */ React.createElement("code", { key: `${e}-${i}`, className: "mr-1 rounded bg-term-bg px-1 py-0.5 text-term-cyan" }, e))), limitations.length > 0 && /* @__PURE__ */ React.createElement("ul", { className: "mt-1 list-disc pl-5 text-term-muted" }, limitations.map((l, i) => /* @__PURE__ */ React.createElement("li", { key: `${l}-${i}` }, l)))));
}
export { AIOpinionCard as default };
