"""Public, unauthenticated endpoints for the landing page.

Only aggregate model accuracy is exposed: no symbols, prices, forecasts or
user data. Cached in-process (the bundle changes weekly).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.forecasting.v4.store import get_bundle

router = APIRouter(prefix="/api/public", tags=["public"])


@router.get("/model")
def model_summary() -> dict:
    bundle = get_bundle()
    if bundle is None:
        raise HTTPException(status_code=503, detail="no model installed")
    horizons = {}
    for h, r in (bundle.report.get("horizons") or {}).items():
        rng, dd, out, up = (r.get(k) or {} for k in ("range", "drop_risk", "out", "up"))
        horizons[h] = {
            "range_coverage_80": rng.get("coverage_80"),
            "range_coverage_by_year": rng.get("coverage_by_year"),
            "drop_risk_skill": dd.get("skill"),
            "out_ic": out.get("ic_mean"),
            "out_ic_t": out.get("ic_t"),
            "out_decile_spread": out.get("decile_spread"),
            "out_deciles": out.get("deciles"),
            "up_skill": up.get("skill"),
            "up_share": up.get("up_share"),
        }
    return {
        "version": bundle.version,
        "trained_at": bundle.trained_at,
        "data_start": bundle.data_start,
        "data_end": bundle.data_end,
        "universe_size": len(bundle.universe),
        "first_test_year": bundle.report.get("first_test_year"),
        "horizons": horizons,
    }
