import { useQuery } from '@tanstack/react-query';
import { getMarketsOverview } from '../api/markets';

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

export default useMarketLiquidity;
