"""Deterministic analytics (Milestone 2): technical, fundamentals, valuation, quality, events.

Pure functions only: no network, no randomness, no wall-clock reads.
Identical inputs -> identical outputs.
"""

from .common import (
    DEGRADED,
    OK,
    UNAVAILABLE,
    MetricResult,
    unavailable,
)

__all__ = ["OK", "DEGRADED", "UNAVAILABLE", "MetricResult", "unavailable"]
