"""Deterministic fundamental ratios (Milestone 2). Pure functions, no network."""

from .ratios import (
    debt_to_assets,
    debt_to_equity,
    earnings_growth,
    gross_margin,
    interest_coverage,
    net_margin,
    operating_margin,
    revenue_growth,
    roe,
    roic,
)

__all__ = [
    "revenue_growth",
    "earnings_growth",
    "gross_margin",
    "operating_margin",
    "net_margin",
    "debt_to_equity",
    "debt_to_assets",
    "interest_coverage",
    "roe",
    "roic",
]
