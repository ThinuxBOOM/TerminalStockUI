import { useQuery } from "@tanstack/react-query";
import {
  getMarketLiquidity,
  getMarketLiquidityHistory,
  getMarketsOverview,
  normalizeLiquidityHistoryWindow,
} from "../api/markets";
import { getMarketLiquidationProxy } from "../api/liquidation";

const MARKETS_OVERVIEW_KEY = ["markets-overview"];

function marketLiquidityKey(mic) {
  return ["market-liquidity", String(mic ?? "").trim().toUpperCase()];
}

function marketLiquidityHistoryKey(mic, window = "1D") {
  return [
    "market-liquidity-history",
    String(mic ?? "").trim().toUpperCase(),
    normalizeLiquidityHistoryWindow(window),
  ];
}

function useMarketLiquidity() {
  return useQuery({
    queryKey: [...MARKETS_OVERVIEW_KEY],
    queryFn: getMarketsOverview,
    staleTime: 120000,
    gcTime: 600000,
    retry: false,
  });
}

function useMarketDetail(mic, enabled) {
  const upper = String(mic ?? "").trim().toUpperCase();
  return useQuery({
    queryKey: [...marketLiquidityKey(upper)],
    queryFn: () => getMarketLiquidity(upper),
    enabled,
    staleTime: 120000,
    gcTime: 600000,
    retry: false,
  });
}

// Per-market liquidity history (future GET /api/markets/{mic}/liquidity/history).
// Backend not deployed yet: fail fast with no retry so cards render their
// placeholder instead of hanging. Callers must pass expanded-only `enabled`.
function useMarketLiquidityHistory(mic, window = "1D", enabled = true) {
  const upper = String(mic ?? "").trim().toUpperCase();
  const w = normalizeLiquidityHistoryWindow(window);
  const on = enabled && upper !== "";
  return useQuery({
    queryKey: [...marketLiquidityHistoryKey(upper, w)],
    queryFn: () => getMarketLiquidityHistory(upper, w),
    enabled: on,
    staleTime: 120000,
    gcTime: 600000,
    retry: false,
  });
}

// Per-market liquidation-PROXY (GET /api/markets/{mic}/liquidation-proxy).
// Honest PROXY data only; on missing backend the query errors to ErrorState
// (never fakes rows).
function useMarketLiquidationProxy(mic, opts = {}, enabled = true) {
  const upper = String(mic ?? "").trim().toUpperCase();
  const limit = Math.min(100, Math.max(1, Number(opts?.limit ?? 20) || 20));
  const sort = String(opts?.sort ?? "intensity").trim().toLowerCase() === "symbol" ? "symbol" : "intensity";
  return useQuery({
    queryKey: ["market-liquidation", upper, sort, limit],
    queryFn: ({ signal }) => getMarketLiquidationProxy(upper, { limit, sort, signal }),
    enabled: enabled && upper !== "",
    staleTime: 120000,
    gcTime: 600000,
    retry: false,
  });
}

// --- Stub hooks (no auth, no network) --------------------------------------
// Placeholders for future backend surfaces. They never fetch and never need
// auth; they return an explicit disabled/placeholder state so callers can
// render honestly while the endpoints are unimplemented.
function useMarketBreadthStub(mic) {
  const upper = String(mic ?? "").trim().toUpperCase();
  return {
    data: null,
    isLoading: false,
    isError: false,
    error: null,
    placeholder: true,
    disabledReason: `market-breadth stub for ${upper || "UNKNOWN"} (no auth, no backend yet)`,
  };
}

function useMarketTurnoverStub(mic) {
  const upper = String(mic ?? "").trim().toUpperCase();
  return {
    data: null,
    isLoading: false,
    isError: false,
    error: null,
    placeholder: true,
    disabledReason: `market-turnover stub for ${upper || "UNKNOWN"} (no auth, no backend yet)`,
  };
}

export {
  MARKETS_OVERVIEW_KEY,
  marketLiquidityHistoryKey,
  marketLiquidityKey,
  useMarketBreadthStub,
  useMarketDetail,
  useMarketLiquidationProxy,
  useMarketLiquidity,
  useMarketLiquidityHistory,
  useMarketTurnoverStub,
};

export default useMarketLiquidity;
