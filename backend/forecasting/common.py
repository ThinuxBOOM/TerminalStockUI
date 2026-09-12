"""Shared forecast envelope and horizon constants (Milestone 3).

Every forecast carries model/feature/data versions plus a timestamp so runs
are reproducible and auditable. Library code never reads the wall clock:
callers pass `as_of` explicitly (tests use fixed stamps).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

FORECAST_HORIZONS = (5, 21, 63)

TARGET_DIRECTION = "direction_probability"
TARGET_RETURN_RANGE = "expected_return_range"
TARGET_VOL_REGIME = "volatility_regime"
TARGET_DRAWDOWN = "drawdown_probability"

OK = "ok"
DEGRADED = "degraded"
UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ForecastResult:
    """One baseline forecast with full provenance.

    Attributes:
        target: One of the TARGET_* constants.
        horizon_days: Forecast horizon in trading days (5/21/63).
        value: Payload (float probability, dict range, or regime label).
        formula: Human-readable method description.
        model_name / model_version: Which baseline produced this.
        feature_version: Feature-schema version consumed.
        data_version: Upstream data snapshot id (provenance, caller-supplied).
        as_of: ISO timestamp of the forecast (caller-supplied, never implicit).
        quality_flag / reason: Availability contract, mirroring analytics.
    """

    target: str
    horizon_days: int
    value: Any
    formula: str
    model_name: str
    model_version: str
    feature_version: str
    data_version: str = "unspecified"
    as_of: str | None = None
    quality_flag: str = OK
    reason: str | None = None

    def as_tuple(self) -> tuple[Any, str, str, str]:
        return (self.value, self.formula, self.model_version, self.quality_flag)


__all__ = [
    "FORECAST_HORIZONS",
    "TARGET_DIRECTION",
    "TARGET_RETURN_RANGE",
    "TARGET_VOL_REGIME",
    "TARGET_DRAWDOWN",
    "ForecastResult",
]
