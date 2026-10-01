import { useCallback, useEffect, useState } from "react";
import { useAuth } from "./useAuth";

// Holdings saved in this browser per signed-in user: [{symbol, amount}].
const PORTFOLIO_KEY = "onemarket.portfolio.v1";

function portfolioKey(userId) {
  return `${PORTFOLIO_KEY}:${userId || "anonymous"}`;
}

function sanitize(list) {
  if (!Array.isArray(list)) return [];
  const out = [];
  const seen = new Set();
  for (const h of list) {
    const symbol = String(h?.symbol ?? "").trim().toUpperCase();
    const amount = Number(h?.amount);
    if (!symbol || seen.has(symbol) || !Number.isFinite(amount)) continue;
    seen.add(symbol);
    out.push({ symbol, amount });
  }
  return out.slice(0, 25);
}

function load(key) {
  try {
    return sanitize(JSON.parse(localStorage.getItem(key) ?? "[]"));
  } catch {
    return [];
  }
}

function usePortfolio() {
  const { user } = useAuth();
  const key = portfolioKey(user?.id);
  const [store, setStore] = useState(() => ({ key, holdings: load(key) }));
  if (store.key !== key) setStore({ key, holdings: load(key) });
  useEffect(() => {
    try {
      localStorage.setItem(store.key, JSON.stringify(store.holdings));
    } catch {
      // storage can be unavailable (private mode); the page still works
    }
  }, [store]);
  const setHoldings = useCallback((next) => {
    setStore((s) => ({ key: s.key, holdings: sanitize(typeof next === "function" ? next(s.holdings) : next) }));
  }, []);
  return { holdings: store.key === key ? store.holdings : load(key), setHoldings };
}

export { portfolioKey, sanitize, usePortfolio as default };
