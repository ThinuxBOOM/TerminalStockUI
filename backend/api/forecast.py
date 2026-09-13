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

#: Display bands for the direction probability. Thresholds mirror the
#: frontend placeholders (0.64 -> "moderately positive") so live responses
#: render the same labels the UI was designed around.
LABEL_BANDS: tuple[tuple[float, str], ...] = (
    (0.65, "clearly positive"),
    (0.57, "moderately positive"),
    (0.52, "slightly positive"),
)


def direction_label(probability: float) -> str:
    """Human label for a direction probability (deterministic bands)."""
    try:
        prob = float(probability)
    except (TypeError, ValueError):
        return "neutral"
    for edge, label in LABEL_BANDS:
        if prob >= edge:
            return label
        if prob <= 1.0 - edge:
            return label.replace("positive", "negative")
    return "neutral"


def forecast_drivers(result: dict) -> tuple[list[str], list[str]]:
    """Derive bull/bear driver strings from computed ensemble values only.

    Every string quotes a number already present in the response — no
    narrative is invented. Cap 4 items per side; empty side renders as
    "unavailable" in the UI (honest, never zero-filled).
    """
    why: list[str] = []
    risks: list[str] = []
    horizon = result.get("horizon_days", "?")
    components = result.get("components") or {}
    for name in sorted(components):
        try:
            value = float(components[name])
        except (TypeError, ValueError):
            continue
        if value > 0.5:
            why.append(f"{name} implies up (p={value:.2f})")
        elif value < 0.5:
            risks.append(f"{name} implies down (p={value:.2f})")
    band = result.get("expected_return_range") or {}
    mid = band.get("mid")
    if isinstance(mid, bool):
        mid = None
    if isinstance(mid, (int, float)):
        (why if mid >= 0 else risks).append(
            f"expected {horizon}d return mid {mid:+.1%}"
        )
    regime = result.get("volatility_regime")
    if regime in ("elevated", "extreme"):
        risks.append(f"volatility regime: {regime}")
    drawdown = result.get("drawdown_probability")
    if isinstance(drawdown, bool):
        drawdown = None
    if isinstance(drawdown, (int, float)):
        if drawdown >= 0.25:
            risks.append(f"large-drawdown probability {drawdown:.0%} over {horizon}d")
        elif drawdown <= 0.10:
            why.append(f"large-drawdown probability low ({drawdown:.0%})")
    return why[:4], risks[:4]


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
    # Display fields the terminal UI renders (derived, never invented):
    # label bands, data-quality passthrough, engine identity, bull/bear
    # drivers quoted from computed components, evidence = model members.
    why, risks = forecast_drivers(result)
    provenance = result.get("provenance") or {}
    payload["label"] = direction_label(result.get("direction_probability", 0.5))
    payload["quality_grade"] = str(provenance.get("quality_grade", "U")).upper() or "U"
    payload["provider"] = "deterministic-engine"
    payload["why"] = why
    payload["risks"] = risks
    payload["evidence_ids"] = list(result.get("model_members", []))
    return payload
