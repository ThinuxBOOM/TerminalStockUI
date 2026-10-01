import { api, normalizeSymbolParam } from "./client";

async function getSymbolRisk(symbol, opts = {}) {
  const sym = normalizeSymbolParam(symbol);
  const { data } = await api.get(`/api/risk/${encodeURIComponent(sym)}`, {
    timeout: 60000,
    ...(opts.signal ? { signal: opts.signal } : {}),
  });
  return data;
}

// holdings: [{symbol, weight}] (weights in any positive units; normalized server-side).
async function postPortfolioRisk(holdings, lookback = 252, opts = {}) {
  const { data } = await api.post(
    "/api/risk/portfolio",
    { holdings: holdings.map((h) => ({ symbol: normalizeSymbolParam(h.symbol), weight: Number(h.weight) })), lookback },
    { timeout: 90000, ...(opts.signal ? { signal: opts.signal } : {}) },
  );
  return data;
}

export { getSymbolRisk, postPortfolioRisk };
