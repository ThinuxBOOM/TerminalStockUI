// Strongest outperformance signals today (top and bottom of the S&P 500 ranking).
import { api } from "./client";

async function getTopSignals(horizon = 21, n = 8, opts = {}) {
  const h = [1, 7, 14, 21].includes(Number(horizon)) ? Number(horizon) : 21;
  const { data } = await api.get("/api/signals/top", {
    params: { horizon: h, n },
    timeout: 30000,
    ...(opts?.signal ? { signal: opts.signal } : {}),
  });
  return {
    horizon: Number(data?.horizon ?? h),
    top: Array.isArray(data?.top) ? data.top : [],
    bottom: Array.isArray(data?.bottom) ? data.bottom : [],
    count: Number(data?.count ?? 0),
    as_of: data?.as_of ?? null,
    model_version: data?.model_version ?? null,
    measured: data?.measured ?? {},
    disclosure: data?.disclosure ?? "",
  };
}

export { getTopSignals };
