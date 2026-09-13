import { useEffect, useMemo, useRef } from 'react';
import {
  createChart,
  ColorType,
  type IChartApi,
  type UTCTimestamp,
} from 'lightweight-charts';
import type { Provenance } from '../../api/client';
import ProvenanceBadge from '../../components/ProvenanceBadge';
import FreshnessBadge from '../../components/FreshnessBadge';

export type Candle = {
  time: string; // YYYY-MM-DD
  open: number;
  high: number;
  low: number;
  close: number;
};

function toUTCTime(d: string): UTCTimestamp {
  return Math.floor(new Date(d + 'T12:00:00Z').getTime() / 1000) as UTCTimestamp;
}

/**
 * Deterministic placeholder series — loading shimmer ONLY.
 * It must never be rendered as chart content: while live bars are in
 * flight the chart shows this wave with an explicit "placeholder" caption;
 * on error/empty the panel shows an honest unavailable state instead.
 */
export function seedCandles(symbol: string, n = 60): Candle[] {
  let h = 0;
  for (const c of symbol) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  let px = 100 + (h % 80);
  const out: Candle[] = [];
  const today = Date.now();
  for (let i = n - 1; i >= 0; i--) {
    const drift = Math.sin((h + i) / 7) * 1.5;
    const open = px;
    const close = Math.max(1, open + drift);
    out.push({
      time: new Date(today - i * 86400000).toISOString().slice(0, 10),
      open,
      high: Math.max(open, close) * 1.01,
      low: Math.min(open, close) * 0.99,
      close,
    });
    px = close;
  }
  return out;
}

export default function PriceChart({
  symbol,
  data,
  loading = false,
  error = null,
  provenance = null,
}: {
  symbol: string;
  /** Live candles from GET /api/market_data/bars (null/empty → unavailable). */
  data?: Candle[] | null;
  loading?: boolean;
  error?: string | null;
  /** Bars provenance envelope — badges render from this, never invented. */
  provenance?: Provenance | null;
}) {
  const ref = useRef<HTMLDivElement>(null);
  // Placeholder is memoized per symbol and used ONLY for the loading state.
  const placeholder = useMemo(() => seedCandles(symbol), [symbol]);
  const live = data && data.length > 0 ? data : null;
  const showPlaceholder = !live && loading && !error;
  const candles = live ?? (showPlaceholder ? placeholder : []);

  useEffect(() => {
    if (!ref.current || candles.length === 0) return;
    const chart: IChartApi = createChart(ref.current, {
      layout: { background: { type: ColorType.Solid, color: '#0f141d' }, textColor: '#8b94a7' },
      grid: { vertLines: { color: '#1c2433' }, horzLines: { color: '#1c2433' } },
      height: 300,
    });
    const series = chart.addCandlestickSeries({
      upColor: '#3ddc84',
      downColor: '#ff5c5c',
      wickUpColor: '#3ddc84',
      wickDownColor: '#ff5c5c',
      borderVisible: false,
    });
    series.setData(candles.map((c) => ({ ...c, time: toUTCTime(c.time) })));
    chart.timeScale().fitContent();
    const ro = new ResizeObserver(() => {
      if (ref.current) chart.applyOptions({ width: ref.current.clientWidth });
    });
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.remove();
    };
  }, [symbol, candles]);

  if (!live && !showPlaceholder) {
    return (
      <div role="status">
        <p className="py-8 text-center text-xs text-term-muted">
          Price history unavailable —{' '}
          {error ?? 'the bars endpoint returned no candles for this symbol.'} No
          placeholder is shown in place of market data.
        </p>
        <p className="mt-1 text-[10px] text-term-muted">
          source: GET /api/market_data/bars · symbol {symbol}
        </p>
        {provenance && (
          <div className="mt-2 flex flex-wrap gap-2">
            <ProvenanceBadge p={provenance} />
            <FreshnessBadge p={provenance} />
          </div>
        )}
      </div>
    );
  }

  return (
    <div>
      <div ref={ref} className="w-full" />
      {showPlaceholder ? (
        <p className="mt-1 text-[10px] text-term-amber" role="status">
          loading live bars from /api/market_data/bars — placeholder wave, not market
          data · {candles.length} bars
        </p>
      ) : (
        <p className="mt-1 text-[10px] text-term-muted">
          live bars from /api/market_data/bars · {candles.length} bars
          {provenance?.fallback_used ? ' · server-flagged fallback (see badge)' : ''}
        </p>
      )}
      {provenance && !showPlaceholder && (
        <div className="mt-2 flex flex-wrap gap-2">
          <ProvenanceBadge p={provenance} />
          <FreshnessBadge p={provenance} />
        </div>
      )}
    </div>
  );
}
