import { useQuery } from "@tanstack/react-query";
import {
  getMarketLiquidity,
  getMarketLiquidityHistory,
  getMarketsOverview,
  normalizeLiquidityHistoryWindow,
} from "../api/markets";

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

export {
  MARKETS_OVERVIEW_KEY,
  marketLiquidityHistoryKey,
  marketLiquidityKey,
  useMarketDetail,
  useMarketLiquidity,
  useMarketLiquidityHistory,
};

export default useMarketLiquidity;
