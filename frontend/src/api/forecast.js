// Forecast engine v4 endpoints. Responses are already shaped by the
// backend; these helpers only guard types the UI does arithmetic on.
import { api, normalizeSymbolParam } from "./client";

const HORIZONS = [1, 7, 14, 21];

function num(v) {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function normalizeForecast(f) {
  if (!f || typeof f !== "object") return null;
  const rng = f.expected_return_range ?? {};
  return {
    ...f,
    horizon_days: Number(f.horizon_days),
    direction_probability: num(f.direction_probability),
    base_rate: num(f.base_rate),
    outperform_probability: num(f.outperform_probability),
    outperform_rank: num(f.outperform_rank),
    drawdown_probability: num(f.drawdown_probability),
    volatility_forecast_annual: num(f.volatility_forecast_annual),
    expected_return_range: { low: num(rng.low), mid: num(rng.mid), high: num(rng.high), coverage: rng.coverage },
    quantiles: f.quantiles ?? {},
    price_quantiles: f.price_quantiles ?? {},
    drivers: { for: f.drivers?.for ?? [], against: f.drivers?.against ?? [] },
    why: Array.isArray(f.why) ? f.why : [],
    risks: Array.isArray(f.risks) ? f.risks : [],
    limitations: Array.isArray(f.limitations) ? f.limitations : [],
    measured: f.measured ?? {},
  };
}

async function getForecastAll(symbol, opts = {}) {
  const sym = normalizeSymbolParam(symbol);
  const { data } = await api.get(`/api/forecast/${encodeURIComponent(sym)}/all`, {
    timeout: 60000,
    ...(opts.signal ? { signal: opts.signal } : {}),
  });
  const horizons = {};
  for (const h of HORIZONS) {
    const f = normalizeForecast(data?.horizons?.[String(h)]);
    if (f) horizons[h] = f;
  }
  return { symbol: data?.symbol ?? sym, horizons };
}

async function getModelCard(opts = {}) {
  const { data } = await api.get("/api/forecast/model", opts.signal ? { signal: opts.signal } : {});
  return data;
}

async function getModelSymbol(symbol, opts = {}) {
  const sym = normalizeSymbolParam(symbol);
  const { data } = await api.get(`/api/forecast/model/${encodeURIComponent(sym)}`, opts.signal ? { signal: opts.signal } : {});
  return data;
}

export { HORIZONS, getForecastAll, getModelCard, getModelSymbol, normalizeForecast };
