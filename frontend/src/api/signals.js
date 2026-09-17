import axios from "axios";

function resolveBaseUrl() {
  const raw = import.meta.env?.VITE_API_BASE_URL;
  const configured = (raw ?? "").trim().replace(/\/+$/, "");
  if (configured) return configured;
  if (typeof window !== "undefined") {
    const { protocol, hostname } = window.location;
    if (protocol === "https:") return "";
    if (hostname && hostname !== "localhost" && hostname !== "127.0.0.1" && hostname !== "[::1]")
      return "";
  }
  return "http://localhost:8000";
}

const api = axios.create({
  baseURL: resolveBaseUrl(),
  timeout: 90000,
  headers: { "Content-Type": "application/json" },
});

function normalizeSignalRow(r) {
  if (!r || typeof r !== "object") return null;
  return {
    symbol: String(r.symbol ?? ""),
    company_name: String(r.company_name ?? r.symbol ?? ""),
    exchange_mic: String(r.exchange_mic ?? ""),
    currency: String(r.currency ?? "USD"),
    price: typeof r.price === "number" ? r.price : null,
    change_pct: typeof r.change_pct === "number" ? r.change_pct : null,
    ensemble_probability: typeof r.ensemble_probability === "number" ? r.ensemble_probability : 0.5,
    news_sentiment: typeof r.news_sentiment === "number" ? r.news_sentiment : 0,
    signal_probability: typeof r.signal_probability === "number" ? r.signal_probability : 0.5,
    verdict: String(r.verdict ?? ""),
    what_it_means: String(r.what_it_means ?? ""),
    confidence: String(r.confidence ?? ""),
    horizon: Number(r.horizon ?? 21),
    provenance: r.provenance ?? null,
  };
}

async function getTopSignals(horizon = 21, perMarket = 5, opts = {}) {
  const h = [1, 7, 14, 21].includes(Number(horizon)) ? Number(horizon) : 21;
  const n = Math.min(10, Math.max(1, Number(perMarket) || 5));
  const { data } = await api.get("/api/signals/top", {
    params: { horizon: h, per_market: n },
    timeout: 90000,
    ...(opts?.signal ? { signal: opts.signal } : {}),
  });
  const markets = {};
  const raw = data?.markets ?? {};
  for (const [mic, bucket] of Object.entries(raw)) {
    markets[mic] = {
      top_buy: Array.isArray(bucket?.top_buy) ? bucket.top_buy.map(normalizeSignalRow).filter(Boolean) : [],
      top_short: Array.isArray(bucket?.top_short) ? bucket.top_short.map(normalizeSignalRow).filter(Boolean) : [],
      count: Number(bucket?.count ?? 0),
    };
  }
  return {
    markets,
    horizon: Number(data?.horizon ?? h),
    formula: String(data?.formula ?? ""),
    skipped: Array.isArray(data?.skipped) ? data.skipped : [],
    provenance: data?.provenance ?? null,
    disclosure: data?.disclosure ?? "Not investment advice.",
  };
}

export { getTopSignals };
