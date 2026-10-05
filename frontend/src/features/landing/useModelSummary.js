import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";

// Measured record of the active model (public, aggregate only). The fallback
// is the record of the bundle that ships with the app, so the page never
// shows empty proof while the API is unreachable.
const FALLBACK = {
  version: "v4-20261001-1507",
  universe_size: 497,
  first_test_year: 2021,
  horizons: {
    1: { range_coverage_80: 0.7974, drop_risk_skill: -0.0096, out_ic: 0.0225, out_ic_t: 4.59, out_decile_spread: 0.00063 },
    7: { range_coverage_80: 0.7996, drop_risk_skill: 0.0449, out_ic: 0.0246, out_ic_t: 2.39, out_decile_spread: 0.00323 },
    14: { range_coverage_80: 0.7945, drop_risk_skill: 0.0496, out_ic: 0.0262, out_ic_t: 1.17, out_decile_spread: 0.00595 },
    21: {
      range_coverage_80: 0.7934,
      drop_risk_skill: 0.0497,
      out_ic: 0.0251,
      out_ic_t: 1.25,
      out_decile_spread: 0.00759,
      up_skill: -0.001,
      up_share: 0.5507,
      range_coverage_by_year: { 2021: 0.8388, 2022: 0.7383, 2023: 0.8101, 2024: 0.7999, 2025: 0.8044, 2026: 0.7574 },
      out_deciles: [0.00382, 0.00644, 0.00761, 0.00747, 0.00835, 0.00881, 0.00928, 0.01078, 0.01105, 0.01143].map((v, i) => ({ decile: i + 1, mean_fwd_log_return: v })),
    },
  },
};

function useModelSummary() {
  const q = useQuery({
    queryKey: ["public-model"],
    queryFn: async ({ signal }) => (await api.get("/api/public/model", { signal, timeout: 8000 })).data,
    staleTime: 3600000,
    retry: false,
  });
  return q.data?.horizons ? q.data : FALLBACK;
}

export { FALLBACK, useModelSummary as default };
