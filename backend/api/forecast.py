"""GET /api/forecast/{symbol}?horizon=5|21|63 — validated ensemble forecast.

Ensemble of existing baselines (historical-drift + momentum + logistic);
quantile bands supply the return range, volatility regime and drawdown
probability. Deterministic, no AI, no network. Every response carries the
provenance envelope + model/feature/data versions + timestamp + the
"Not investment advice" disclosure. Horizons outside {5, 21, 63} -> 422.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.forecasting.common import FORECAST_HORIZONS
from backend.forecasting.service import ForecastService, get_forecast_service

router = APIRouter(prefix="/api/forecast", tags=["forecast"])


@router.get("/{symbol}")
def get_forecast(
    symbol: str,
    horizon: int = Query(default=21, description="Trading-day horizon: 5, 21 or 63"),
    svc: ForecastService = Depends(get_forecast_service),
) -> dict:
    """Forecast one symbol/horizon (ensemble direction + bands + risk)."""
    if int(horizon) not in FORECAST_HORIZONS:
        raise HTTPException(
            status_code=422,
            detail=f"horizon must be one of {list(FORECAST_HORIZONS)}, got {horizon}",
        )
    try:
        result = svc.forecast(symbol, int(horizon))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # Flatten the record mirror out of the wire payload (kept in result["record"]).
    payload = {k: v for k, v in result.items() if k != "record"}
    return payload
