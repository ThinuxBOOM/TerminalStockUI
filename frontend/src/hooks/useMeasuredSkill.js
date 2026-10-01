import { useQuery } from "@tanstack/react-query";
import { getMeasuredSkill, skillForHorizon } from "../api/measuredSkill";

// The table only changes when the evaluation is re-run and deployed.
function useMeasuredSkill(horizon, calibration = null) {
  const q = useQuery({
    queryKey: ["measured-skill"],
    queryFn: ({ signal }) => getMeasuredSkill({ signal }),
    staleTime: 60 * 60 * 1000,
    retry: false,
  });
  return { skill: skillForHorizon(q.data, horizon, calibration), isLoading: q.isLoading, isError: q.isError };
}

export { useMeasuredSkill, useMeasuredSkill as default };
