import axios from "axios";
import { resolveApiBaseUrl } from "./baseUrl";

function resolveBaseUrl() {
  // Shared chain (?api= > localStorage > public/config.js > build > default).
  return resolveApiBaseUrl();
}

const api = axios.create({
  baseURL: resolveBaseUrl(),
  timeout: 20000,
  headers: { "Content-Type": "application/json" },
});

function normalizeArticle(a) {
  if (!a || typeof a !== "object") return null;
  return {
    title: String(a.title ?? "(untitled)"),
    summary: String(a.summary ?? ""),
    url: String(a.url ?? ""),
    created_at: String(a.created_at ?? ""),
    symbols: Array.isArray(a.symbols) ? a.symbols.map((s) => String(s)) : [],
    author: String(a.author ?? ""),
    sentiment: typeof a.sentiment === "number" ? a.sentiment : 0,
    sentiment_label: String(a.sentiment_label ?? "neutral"),
  };
}

async function getNews(symbols = "", limit = 20, opts = {}) {
  const signal = opts?.signal;
  const params = { limit: Math.min(50, Math.max(1, Number(limit) || 20)) };
  if (symbols && String(symbols).trim()) params.symbols = String(symbols).trim().toUpperCase();
  const { data } = await api.get("/api/news", {
    params,
    timeout: 20000,
    ...(signal ? { signal } : {}),
  });
  const articles = Array.isArray(data?.articles) ? data.articles.map(normalizeArticle).filter(Boolean) : [];
  return {
    articles,
    count: articles.length,
    provenance: data?.provenance ?? null,
    disclosure: data?.disclosure ?? "Not investment advice.",
  };
}

async function getSymbolNews(symbol, limit = 10, opts = {}) {
  const sym = String(symbol ?? "").trim().toUpperCase();
  if (!sym) return { articles: [], count: 0, provenance: null };
  const { data } = await api.get(`/api/news/symbol/${encodeURIComponent(sym)}`, {
    params: { limit: Math.min(50, Math.max(1, Number(limit) || 10)) },
    timeout: 20000,
    ...(opts?.signal ? { signal: opts.signal } : {}),
  });
  const articles = Array.isArray(data?.articles) ? data.articles.map(normalizeArticle).filter(Boolean) : [];
  return {
    articles,
    count: articles.length,
    provenance: data?.provenance ?? null,
    disclosure: data?.disclosure ?? "Not investment advice.",
  };
}

export { getNews, getSymbolNews };
