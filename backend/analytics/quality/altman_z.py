"""Altman Z-score, public-manufacturing variant
(Altman, 1968, "Financial Ratios, Discriminant Analysis and the Prediction
of Corporate Bankruptcy").

Formula: Z = 1.2*X1 + 1.4*X2 + 3.3*X3 + 0.6*X4 + 1.0*X5 where
  X1 = working_capital / total_assets
  X2 = retained_earnings / total_assets
  X3 = ebit / total_assets
  X4 = market_value_equity / total_liabilities
  X5 = revenue / total_assets
Zones: Z > 2.99 "safe", 1.81 <= Z <= 2.99 "grey", Z < 1.81 "distress".
Note: calibrated on US public manufacturers; apply to other
sectors/markets with caution (documented limitation, not a silent tweak).
Source fields: see SOURCE_FIELDS.
"""

from __future__ import annotations

from typing import Mapping

from ..common import MetricResult, get_number, unavailable

FORMULA = (
    "Z = 1.2*(WC/TA) + 1.4*(RE/TA) + 3.3*(EBIT/TA) "
    "+ 0.6*(MVE/TL) + 1.0*(Sales/TA) (Altman 1968, public-manufacturing)"
)
SOURCE_FIELDS = (
    "working_capital", "retained_earnings", "ebit",
    "market_value_equity", "total_liabilities", "total_assets", "revenue",
)


def altman_z(fin: Mapping) -> MetricResult:
    """Compute the Altman Z-score and distress zone."""
    missing = [k for k in SOURCE_FIELDS if get_number(fin, k) is None]
    if missing:
        return unavailable(FORMULA, list(SOURCE_FIELDS),
                           f"missing fields: {missing}")
    ta = get_number(fin, "total_assets")
    tl = get_number(fin, "total_liabilities")
    if ta == 0:
        return unavailable(FORMULA, list(SOURCE_FIELDS),
                           "denominator 'total_assets' is zero")
    if tl == 0:
        return unavailable(FORMULA, list(SOURCE_FIELDS),
                           "denominator 'total_liabilities' is zero")
    components = {
        "X1": get_number(fin, "working_capital") / ta,
        "X2": get_number(fin, "retained_earnings") / ta,
        "X3": get_number(fin, "ebit") / ta,
        "X4": get_number(fin, "market_value_equity") / tl,
        "X5": get_number(fin, "revenue") / ta,
    }
    z = (1.2 * components["X1"] + 1.4 * components["X2"]
         + 3.3 * components["X3"] + 0.6 * components["X4"] + components["X5"])
    zone = "safe" if z > 2.99 else ("grey" if z >= 1.81 else "distress")
    return MetricResult({"z": z, "zone": zone, "components": components},
                        FORMULA, SOURCE_FIELDS, "ok")


__all__ = ["altman_z", "FORMULA", "SOURCE_FIELDS"]
