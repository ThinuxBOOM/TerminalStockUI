import React from "react";
import { Link, useSearchParams } from "react-router-dom";
import SearchBox from "../features/search/SearchBox";

const KNOWN_MARKETS = new Set(["XNYS", "XNAS", "XSHG", "XPAR", "XAMS", "XBRU"]);
function normalizeMarket(v) {
  const mic = (v ?? "").trim().toUpperCase();
  if (!mic || mic === "ALL") return "";
  return KNOWN_MARKETS.has(mic) ? mic : "";
}
function SearchPage() {
  const [params] = useSearchParams();
  const q = params.get("q") ?? "";
  const market = normalizeMarket(params.get("market"));
  return (
    <div className="max-w-full">
      <nav className="mb-3 text-xs" aria-label="Breadcrumb"><Link to="/app" className="text-term-muted hover:text-term-text">← Home</Link></nav>
      <h1 className="text-lg font-extrabold text-term-text">Discover — Search</h1>
      <p className="mt-0.5 text-xs text-term-muted">Exchange-aware search across NYSE, Nasdaq, SSE and Euronext. Pick an exact symbol — never auto-resolved.</p>
      <div className="mt-3">
        <SearchBox key={`${q}|${market}`} initial={q} initialMarket={market} />
      </div>
    </div>
  );
}
export { SearchPage as default };
