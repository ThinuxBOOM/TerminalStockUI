import { useQuery } from '@tanstack/react-query';
import { getMarketLiquidity, getMarketsOverview } from '../api/markets';

/**
 * Homepage per-market liquidity+breadth query.
 * staleTime 30s + retry 1 (matches the app-wide QueryClient defaults).
 * Resolves via GET /api/markets/overview when deployed, otherwise via
 * the client-side screener fan-out in getMarketsOverview (marked
 * fallback_used + grade D so the panel gates it honestly).
 */
export function useMarketLiquidity() {
  return useQuery({
    queryKey: ['markets-overview'],
    queryFn: getMarketsOverview,
    staleTime: 30_000,
    retry: 1,
  });
}

/**
 * Per-market detail (rows for graphs). Disabled until the card is expanded
 * so the homepage never fires 6 detail fan-outs on load.
 */
export function useMarketDetail(mic: string, enabled: boolean) {
  const upper = mic.trim().toUpperCase();
  return useQuery({
    queryKey: ['market-liquidity', upper],
    queryFn: () => getMarketLiquidity(upper),
    enabled,
    staleTime: 30_000,
    retry: 1,
  });
}

export default useMarketLiquidity;
