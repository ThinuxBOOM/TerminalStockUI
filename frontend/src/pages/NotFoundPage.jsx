import React from "react";
import { Link } from "react-router-dom";
// Unknown route: offer a way back instead of silently bouncing home.
function NotFoundPage() {
  return /* @__PURE__ */ React.createElement("div", { className: "term-panel mx-auto max-w-xl p-6 text-center" }, /* @__PURE__ */ React.createElement("h1", { className: "text-lg font-bold text-term-text" }, "404 \u2014 no such screen"), /* @__PURE__ */ React.createElement("p", { className: "mt-2 text-sm text-term-muted" }, "That route doesn't exist. Head back to the terminal or search for an instrument."), /* @__PURE__ */ React.createElement("div", { className: "mt-4 flex flex-wrap justify-center gap-2" }, /* @__PURE__ */ React.createElement(Link, { to: "/", className: "term-btn text-xs" }, "HOME"), /* @__PURE__ */ React.createElement(Link, { to: "/search", className: "term-btn-ghost text-xs" }, "SEARCH"), /* @__PURE__ */ React.createElement(Link, { to: "/screener", className: "term-btn-ghost text-xs" }, "SCREENER")));
}
export { NotFoundPage as default };
