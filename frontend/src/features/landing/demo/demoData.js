/* Clearly-labeled DEMO data for the landing-page interactive demo.
 * Fake-but-realistic: deterministic series so the demo feels like the real
 * terminal. NEVER presented as live values — every consumer must render the
 * "Demo data" label next to these numbers. */

function seededSeries(seed, n, start, drift, vol) {
  let s = seed;
  const out = [];
  let v = start;
  for (let i = 0; i < n; i++) {
    s = (s * 1103515245 + 12345) % 2147483648;
    const r = s / 2147483648 - 0.5;
    v += drift + r * vol;
    out.push(Math.round(v * 100) / 100);
  }
  return out;
}

function sma(values, window) {
  return values.map((_, i) => {
    if (i < window - 1) return null;
    let sum = 0;
    for (let j = i - window + 1; j <= i; j++) sum += values[j];
    return Math.round((sum / window) * 100) / 100;
  });
}

export const DEMO_HORIZONS = [1, 7, 14, 21];
export const DEMO_TIMEFRAMES = ["1W", "1M", "3M"];
export const DEMO_INDICATORS = ["SMA20", "VOL"];

export const DEMO_SYMBOLS = [
  {
    symbol: "AAPL",
    name: "Apple Inc.",
    market: "NASDAQ",
    mic: "XNAS",
    currency: "USD",
    price: 193.42,
    changePct: 1.42,
    source: "Demo feed",
    updatedAgo: "12s ago",
    quality: "A",
    freshness: "FRESH",
    spark: seededSeries(42, 32, 188, 0.18, 1.1),
    series: {
      "1W": seededSeries(7, 14, 190.5, 0.2, 1.2),
      "1M": seededSeries(21, 30, 186, 0.25, 1.6),
      "3M": seededSeries(99, 60, 178, 0.28, 2.2),
    },
    volume: seededSeries(5, 30, 52, 0.4, 9).map((v) => Math.max(8, Math.round(v))),
    forecast: {
      1: { prob: 54, direction: "up", confidence: "MEDIUM", quality: "A", model: "trend + momentum", text: "Slight upward lean tomorrow. Expect a fairly balanced day." },
      7: { prob: 58, direction: "up", confidence: "MEDIUM", quality: "A", model: "trend + momentum", text: "Models lean positive over the next 7 trading days, but the edge is modest." },
      14: { prob: 61, direction: "up", confidence: "HIGH", quality: "A", model: "ensemble v3", text: "Positive lean over 2 weeks, backed by steady trend and calm volatility." },
      21: { prob: 64, direction: "up", confidence: "HIGH", quality: "A", model: "ensemble v3", text: "Models currently lean positive over the next 21 trading days." },
    },
  },
  {
    symbol: "NVDA",
    name: "NVIDIA Corp.",
    market: "NASDAQ",
    mic: "XNAS",
    currency: "USD",
    price: 131.88,
    changePct: 2.36,
    source: "Demo feed",
    updatedAgo: "18s ago",
    quality: "A",
    freshness: "FRESH",
    spark: seededSeries(1337, 32, 124, 0.26, 1.8),
    series: {
      "1W": seededSeries(11, 14, 127, 0.3, 2.0),
      "1M": seededSeries(31, 30, 118, 0.45, 2.6),
      "3M": seededSeries(77, 60, 104, 0.5, 3.2),
    },
    volume: seededSeries(9, 30, 68, 0.6, 12).map((v) => Math.max(10, Math.round(v))),
    forecast: {
      1: { prob: 56, direction: "up", confidence: "MEDIUM", quality: "A", model: "trend + momentum", text: "Upward lean tomorrow on strong momentum, with wider swings than usual." },
      7: { prob: 62, direction: "up", confidence: "HIGH", quality: "A", model: "ensemble v3", text: "Positive lean over 7 days. Momentum is doing most of the work here." },
      14: { prob: 66, direction: "up", confidence: "HIGH", quality: "B", model: "ensemble v3", text: "Positive lean over 2 weeks. Higher conviction, higher wobble." },
      21: { prob: 63, direction: "up", confidence: "MEDIUM", quality: "B", model: "ensemble v3", text: "Models lean positive over 21 days, with volatility keeping confidence in check." },
    },
  },
  {
    symbol: "TSLA",
    name: "Tesla Inc.",
    market: "NASDAQ",
    mic: "XNAS",
    currency: "USD",
    price: 248.12,
    changePct: -0.84,
    source: "Demo feed",
    updatedAgo: "24s ago",
    quality: "B",
    freshness: "DELAYED",
    spark: seededSeries(777, 32, 256, -0.28, 2.4),
    series: {
      "1W": seededSeries(13, 14, 252, -0.3, 2.6),
      "1M": seededSeries(55, 30, 262, -0.45, 3.1),
      "3M": seededSeries(123, 60, 274, -0.4, 3.8),
    },
    volume: seededSeries(15, 30, 74, 0.5, 14).map((v) => Math.max(10, Math.round(v))),
    forecast: {
      1: { prob: 47, direction: "down", confidence: "LOW", quality: "B", model: "trend + momentum", text: "Close to a coin flip tomorrow, with a slight downward tilt." },
      7: { prob: 45, direction: "down", confidence: "MEDIUM", quality: "B", model: "ensemble v3", text: "Models lean slightly negative over 7 days. Trend is soft." },
      14: { prob: 44, direction: "down", confidence: "MEDIUM", quality: "B", model: "ensemble v3", text: "Slight negative lean over 2 weeks. Not a strong signal either way." },
      21: { prob: 46, direction: "down", confidence: "LOW", quality: "B", model: "ensemble v3", text: "Near-even odds over 21 days. The honest read: no clear edge here." },
    },
  },
];

export function getDemoSymbol(sym) {
  return DEMO_SYMBOLS.find((d) => d.symbol === sym) || DEMO_SYMBOLS[0];
}

export { sma };
export default DEMO_SYMBOLS;
