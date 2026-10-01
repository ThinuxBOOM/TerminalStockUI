"""GET /api/signals/top — the strongest outperformance signals today.

The top and bottom of the daily S&P 500 ranking from ``forecast_scores``,
with the horizon's measured record. A ranking edge is small and only shows
across many stocks; the response says so.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.screener import _row
from backend.auth.guards import get_current_user
from backend.forecasting.common import FORECAST_HORIZONS
from backend.forecasting.service import measured_for
from backend.forecasting.v4.store import get_bundle, read_scores

router = APIRouter(prefix="/api/signals", tags=["signals"], dependencies=[Depends(get_current_user)])


def _buckets(rows: list[dict], n: int) -> dict:
    """Highest and lowest outperformance ranks, n each, never overlapping."""
    ranked = sorted((r for r in rows if r.get("out_rank") is not None), key=lambda r: r["out_rank"], reverse=True)
    top = [r for r in ranked if r["out_rank"] >= 0.5][:n]
    bottom = [r for r in reversed(ranked) if r["out_rank"] < 0.5][:n]
    return {"top": top, "bottom": bottom, "count": len(ranked)}


@router.get("/top")
def top_signals(
    horizon: int = Query(default=21),
    n: int = Query(default=8, ge=1, le=25),
) -> dict:
    if int(horizon) not in FORECAST_HORIZONS:
        raise HTTPException(status_code=422, detail=f"horizon must be one of {list(FORECAST_HORIZONS)}")
    rows = [_row(r) for r in read_scores(int(horizon), mics=["XNYS", "XNAS"], limit=2000)]
    bundle = get_bundle()
    hr = ((bundle.report.get("horizons") or {}).get(str(horizon), {}) if bundle else {})
    return {
        "horizon": int(horizon),
        **_buckets(rows, n),
        "as_of": max((r["as_of"] for r in rows if r.get("as_of")), default=None),
        "model_version": bundle.version if bundle else None,
        "measured": measured_for(hr),
        "disclosure": "A small statistical edge across many stocks, not a recommendation on any one.",
    }
