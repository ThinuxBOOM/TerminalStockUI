"""Measured walk-forward skill of the direction probabilities.

``measured_skill.json`` is the report written by
``scripts/evaluate_forecasts.py --out backend/forecasting/measured_skill.json``:
pooled Brier skill against the training base rate, per horizon, for one
model version. The forecast payload and ``GET /api/forecast/measured-skill``
serve it so every direction probability can be shown next to its measured
track record.

A report for another model version is ignored (``None``): after a model
change the app says "not measured yet" until the script is re-run.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from backend.forecasting.registry import ENSEMBLE_VERSION

REPORT_PATH = Path(__file__).with_name("measured_skill.json")


@lru_cache(maxsize=1)
def _report() -> dict:
    try:
        report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return report if isinstance(report, dict) else {}


def _ci_pair(ci: object) -> tuple[float, float] | None:
    if not isinstance(ci, list) or len(ci) != 2:
        return None
    try:
        return float(ci[0]), float(ci[1])
    except (TypeError, ValueError):
        return None


def _verdict(ci: tuple[float, float] | None) -> str:
    """worse / better only when the 95% interval excludes zero."""
    if ci is None:
        return "unknown"
    low, high = ci
    if high < 0:
        return "worse"
    if low > 0:
        return "better"
    return "indistinguishable"


_VERDICT_TEXT = {
    "worse": "scored worse than",
    "better": "scored better than",
    "indistinguishable": "were no better than",
}


def measured_skill(horizon: int, calibration_method: str | None = None) -> dict | None:
    """Pooled skill for one horizon, matching how the probability was calibrated.

    ``calibration_method`` is the forecast's own: "isotonic"/"platt" use the
    isotonic-calibrated scores, anything else the shrinkage ones. ``None``
    when there is no report for the running model version or this horizon.
    """
    report = _report()
    if report.get("model_version") != ENSEMBLE_VERSION:
        return None
    row = (report.get("horizons") or {}).get(str(int(horizon)))
    if not isinstance(row, dict):
        return None
    isotonic = str(calibration_method or "").startswith(("isotonic", "platt"))
    skill = row.get("skill_isotonic" if isotonic else "skill")
    ci = _ci_pair(row.get("skill_isotonic_ci95" if isotonic else "skill_ci95"))
    if not isinstance(skill, (int, float)):
        return None
    verdict = _verdict(ci)
    params = report.get("params") or {}
    symbols = row.get("symbols") or len(params.get("universe") or [])
    summary = (
        f"In walk-forward tests on {symbols} stocks, {int(horizon)}-day direction "
        f"probabilities {_VERDICT_TEXT.get(verdict, 'were compared with')} the "
        f"historical base rate (skill {skill:+.3f}"
        + (f", 95% CI {ci[0]:+.3f} to {ci[1]:+.3f})." if ci else ").")
    )
    return {
        "horizon_days": int(horizon),
        "model_version": ENSEMBLE_VERSION,
        "calibration": "isotonic" if isotonic else "shrinkage",
        "skill": float(skill),
        "ci95": list(ci) if ci else None,
        "verdict": verdict,
        "symbols": int(symbols or 0),
        "points": int(row.get("points") or 0),
        "as_of": report.get("as_of"),
        "summary": summary,
    }


def measured_skill_table() -> dict:
    """Every measured horizon (shrinkage and isotonic) for the UI."""
    report = _report()
    horizons = sorted(int(h) for h in (report.get("horizons") or {}))
    return {
        "model_version": ENSEMBLE_VERSION,
        "measured": report.get("model_version") == ENSEMBLE_VERSION,
        "as_of": report.get("as_of"),
        "method": "scripts/evaluate_forecasts.py",
        "horizons": {
            str(h): {
                "shrinkage": measured_skill(h, "shrinkage"),
                "isotonic": measured_skill(h, "isotonic"),
            }
            for h in horizons
        },
    }
