import { api } from "./client";

// Daily forecast scores, filtered and sorted server-side.
async function getScreener(params = {}, opts = {}) {
  const clean = Object.fromEntries(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== ""));
  const { data } = await api.get("/api/screener", {
    params: clean,
    timeout: 30000,
    ...(opts.signal ? { signal: opts.signal } : {}),
  });
  return { ...data, rows: Array.isArray(data?.rows) ? data.rows : [] };
}

export { getScreener };
