"""Shared result envelope and validation helpers for deterministic analytics.

Contract (Milestone 2 acceptance):
  * Every metric returns a MetricResult of (value, formula, source_fields,
    quality_flag) and never raises on missing data.
  * Missing/insufficient data -> quality_flag == "unavailable" with a reason.
  * quality_flag == "degraded" means computable but imperfect input
    (e.g. NaN observations dropped, fallback field derivation used).
  * No network, no randomness, no wall-clock reads anywhere in this package.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import pandas as pd

OK = "ok"
DEGRADED = "degraded"
UNAVAILABLE = "unavailable"
QUALITY_FLAGS = (OK, DEGRADED, UNAVAILABLE)


@dataclass(frozen=True)
class MetricResult:
    """Standard envelope for one computed metric.

    Attributes:
        value: Computed payload (float, dict, Series, DataFrame) or None
            when quality_flag == "unavailable".
        formula: Human-readable formula / method description.
        source_fields: Canonical input field names consumed.
        quality_flag: One of "ok" | "degraded" | "unavailable".
        reason: Required when unavailable; optional note when degraded.
    """

    value: Any
    formula: str
    source_fields: tuple[str, ...]
    quality_flag: str
    reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_fields", tuple(self.source_fields))
        if self.quality_flag not in QUALITY_FLAGS:
            raise ValueError(f"unknown quality_flag {self.quality_flag!r}")
        if self.quality_flag == UNAVAILABLE and not self.reason:
            raise ValueError("unavailable results require a reason")

    def as_tuple(self) -> tuple[Any, str, list[str], str]:
        """Return the (value, formula, source_fields, quality_flag) tuple."""
        return (self.value, self.formula, list(self.source_fields), self.quality_flag)

    @property
    def is_available(self) -> bool:
        return self.quality_flag != UNAVAILABLE


def unavailable(
    formula: str, source_fields: Sequence[str], reason: str
) -> MetricResult:
    """Build an "unavailable" result for missing/insufficient data."""
    return MetricResult(
        value=None,
        formula=formula,
        source_fields=tuple(source_fields),
        quality_flag=UNAVAILABLE,
        reason=reason,
    )


def _coerce_series(values: Any, field_name: str) -> tuple[pd.Series | None, str | None]:
    if values is None:
        return None, f"missing required field '{field_name}'"
    if isinstance(values, pd.Series):
        series = values
    else:
        try:
            series = pd.Series(values)
        except (ValueError, TypeError):
            return None, f"field '{field_name}' is not a numeric series"
    try:
        series = series.astype(float)
    except (ValueError, TypeError):
        return None, f"field '{field_name}' contains non-numeric values"
    return series, None


def validate_series(
    values: Any, field_name: str, min_length: int = 1
) -> tuple[pd.Series | None, int, str | None]:
    """Coerce to a float Series and enforce a minimum valid length.

    Returns (series_without_nan, n_dropped_nan, error_reason_or_None).
    The surviving Series keeps its original index (for time alignment).
    """
    series, error = _coerce_series(values, field_name)
    if error is not None:
        return None, 0, error
    # Treat +/-inf as missing (dropna alone keeps inf and poisons
    # rolling/ewm windows). Count them as dropped for quality grading.
    series = series.replace([float("inf"), float("-inf")], float("nan"))
    valid = series.dropna()
    dropped = int(len(series) - len(valid))
    if len(valid) < min_length:
        return (
            None,
            dropped,
            f"field '{field_name}': only {len(valid)} valid observations, "
            f"need >= {min_length}",
        )
    return valid, dropped, None


def align_series(
    fields: Mapping[str, Any], min_length: int = 1
) -> tuple[pd.DataFrame | None, int, str | None]:
    """Coerce several named series, inner-align on the union index, drop NaN rows.

    Returns (aligned_frame, n_dropped_rows, error_reason_or_None).
    """
    columns: dict[str, pd.Series] = {}
    for name, vals in fields.items():
        series, error = _coerce_series(vals, name)
        if error is not None:
            return None, 0, error
        columns[name] = series
    frame = pd.DataFrame(columns)
    n_before = len(frame)
    # Same inf rule as validate_series: inf rows are missing, not valid.
    frame = frame.replace([float("inf"), float("-inf")], float("nan"))
    frame = frame.dropna()
    dropped = int(n_before - len(frame))
    if len(frame) < min_length:
        names = sorted(columns)
        return (
            None,
            dropped,
            f"fields {names}: only {len(frame)} complete aligned observations, "
            f"need >= {min_length}",
        )
    return frame, dropped, None


def quality_of(n_dropped: int) -> str:
    """'degraded' if any input observations were dropped, else 'ok'."""
    return DEGRADED if n_dropped > 0 else OK


def get_number(data: Mapping[str, Any], key: str) -> float | None:
    """Extract a finite float from a statement mapping; None/NaN -> None.

    Booleans are NOT numbers here (``float(True) == 1.0`` would silently
    turn a flag into a statement value); bool fields read as missing.
    """
    if not isinstance(data, Mapping):
        return None
    value = data.get(key, None)
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (ValueError, TypeError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def missing_fields(data: Mapping[str, Any], required: Sequence[str]) -> list[str]:
    """List required keys that are absent, None, NaN, or non-numeric."""
    return [key for key in required if get_number(data, key) is None]
