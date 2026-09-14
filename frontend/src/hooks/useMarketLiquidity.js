import { useQuery } from "@tanstack/react-query";
import { getMarketLiquidity, getMarketsOverview } from "../api/markets";
const MARKETS_OVERVIEW_KEY = ["markets-overview"];
function marketLiquidityKey(mic) {
  return ["market-liquidity", String(mic ?? "").trim().toUpperCase()];
}
function useMarketLiquidity() {
  return useQuery({
    queryKey: [...MARKETS_OVERVIEW_KEY],
    queryFn: getMarketsOverview,
    staleTime: 3e4,
    retry: 1
  });
}
function useMarketDetail(mic, enabled) {
  const upper = String(mic ?? "").trim().toUpperCase();
  return useQuery({
    queryKey: [...marketLiquidityKey(upper)],
    queryFn: () => getMarketLiquidity(upper),
    enabled,
    staleTime: 3e4,
    retry: 1
  });
}
var stdin_default = useMarketLiquidity;
export { MARKETS_OVERVIEW_KEY, stdin_default as default, marketLiquidityKey, useMarketDetail, useMarketLiquidity };
