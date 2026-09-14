import React from "react";
import { useSearchParams } from "react-router-dom";
import SearchBox from "../features/search/SearchBox";
const KNOWN_MARKETS = /* @__PURE__ */ new Set(["XNYS", "XNAS", "XSHG", "XPAR", "XAMS", "XBRU"]);
function normalizeMarket(v) {
  const mic = (v ?? "").trim().toUpperCase();
  if (!mic || mic === "ALL") return "";
  return KNOWN_MARKETS.has(mic) ? mic : "";
}
function SearchPage() {
  const [params] = useSearchParams();
  const q = params.get("q") ?? "";
  const market = normalizeMarket(params.get("market"));
  return /* @__PURE__ */ React.createElement("div", null, /* @__PURE__ */ React.createElement("h1", { className: "mb-3 text-sm tracking-widest text-term-muted" }, "SEARCH \xB7 UNIFIED EXCHANGE-AWARE"), /* @__PURE__ */ React.createElement(SearchBox, { key: `${q}|${market}`, initial: q, initialMarket: market }));
}
export { SearchPage as default };
