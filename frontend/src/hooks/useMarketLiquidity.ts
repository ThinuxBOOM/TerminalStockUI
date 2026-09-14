import { useQuery } from '@tanstack/react-query';
import { getMarketLiquidity, getMarketsOverview } from '../api/markets';

/** Canonical TanStack keys for market breadth (shared to dedupe in-flight). */
export const MARKETS_OVERVIEW_KEY = ['markets-overview'] as const;
export function marketLiquidityKey(mic: string): readonly [string, string] {
  return ['market-liquidity', String(mic ?? '').trim().toUpperCase()] as const;
}

/**
 * Homepage per-market liquidity+breadth query.
 * staleTime 30s + retry 1 (matches the app-wide QueryClient defaults).
 * Resolves via GET /api/markets/overview when deployed, otherwise via
 * the client-side screener fan-out in getMarketsOverview (marked
 * fallback_used + grade D so the panel gates it honestly).
 */
export function useMarketLiquidity() {
  return useQuery({
    queryKey: [...MARKETS_OVERVIEW_KEY],
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
  const upper = String(mic ?? '').trim().toUpperCase();
  return useQuery({
    queryKey: [...marketLiquidityKey(upper)],
    queryFn: () => getMarketLiquidity(upper),
    enabled,
    staleTime: 30_000,
    retry: 1,
  });
}

export default useMarketLiquidity;
