// Measured walk-forward skill of the direction probabilities (pooled over
// many symbols, per horizon), from GET /api/forecast/measured-skill and the
// `measured_skill` field of every forecast.
import { api } from "./client";

const VERDICTS = new Set(["worse", "better", "indistinguishable", "unknown"]);

function finite(v) {
  return typeof v === "number" && Number.isFinite(v);
}

// One horizon's entry, or null when it is missing or malformed.
function normalizeMeasuredSkill(raw) {
  if (!raw || typeof raw !== "object" || !finite(raw.skill)) return null;
  const ci = Array.isArray(raw.ci95) && raw.ci95.length === 2 && raw.ci95.every(finite) ? [raw.ci95[0], raw.ci95[1]] : null;
  return {
    horizon_days: finite(raw.horizon_days) ? raw.horizon_days : null,
    calibration: raw.calibration === "isotonic" ? "isotonic" : "shrinkage",
    skill: raw.skill,
    ci95: ci,
    verdict: VERDICTS.has(raw.verdict) ? raw.verdict : "unknown",
    symbols: finite(raw.symbols) ? raw.symbols : null,
    points: finite(raw.points) ? raw.points : null,
    as_of: typeof raw.as_of === "string" ? raw.as_of : null,
    summary: typeof raw.summary === "string" ? raw.summary : "",
  };
}

function normalizeMeasuredSkillTable(raw) {
  const horizons = {};
  for (const [h, entry] of Object.entries(raw?.horizons ?? {})) {
    horizons[h] = {
      shrinkage: normalizeMeasuredSkill(entry?.shrinkage),
      isotonic: normalizeMeasuredSkill(entry?.isotonic),
    };
  }
  return {
    measured: raw?.measured === true,
    model_version: typeof raw?.model_version === "string" ? raw.model_version : null,
    as_of: typeof raw?.as_of === "string" ? raw.as_of : null,
    horizons,
  };
}

// `calibration` is "shrinkage" or "isotonic" when the caller knows which one
// produced its probabilities (the screener and signals scan always use
// shrinkage); otherwise the less flattering of the two is shown.
function skillForHorizon(table, horizon, calibration = null) {
  const entry = table?.horizons?.[String(horizon)];
  if (calibration) return entry?.[calibration] ?? null;
  const options = [entry?.shrinkage, entry?.isotonic].filter(Boolean);
  if (options.length === 0) return null;
  return options.reduce((a, b) => (b.skill < a.skill ? b : a));
}

async function getMeasuredSkill(opts) {
  const { data } = await api.get("/api/forecast/measured-skill", opts?.signal ? { signal: opts.signal } : {});
  return normalizeMeasuredSkillTable(data);
}

export { getMeasuredSkill, normalizeMeasuredSkill, normalizeMeasuredSkillTable, skillForHorizon };
