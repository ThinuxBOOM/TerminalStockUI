import { useEffect, useRef } from 'react';
import {
  createChart,
  ColorType,
  type IChartApi,
  type UTCTimestamp,
} from 'lightweight-charts';

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

/** Deterministic placeholder series so the chart renders before backend history lands. */
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

export default function PriceChart({ symbol, data }: { symbol: string; data?: Candle[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const candles = data ?? seedCandles(symbol);

  useEffect(() => {
    if (!ref.current) return;
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
  }, [symbol]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div>
      <div ref={ref} className="w-full" />
      <p className="mt-1 text-[10px] text-term-muted">
        chart: seeded client-side placeholder until /api/market_data/history lands · {candles.length} bars
      </p>
    </div>
  );
}
