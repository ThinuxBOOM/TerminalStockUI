"""Piotroski F-score (Piotroski, 2000, "Value Investing: The Use of Historical
Financial Statement Information to Separate Winners from Losers").

Formula: F = sum of 9 binary criteria (1 point each):
  Profitability
    1. ROA = net_income / total_assets > 0
    2. operating_cash_flow > 0
    3. dROA = ROA_t - ROA_{t-1} > 0
    4. Accruals: operating_cash_flow > net_income
  Leverage / liquidity / funding
    5. dLEVER = (long_term_debt/total_assets)_t
                - (long_term_debt/total_assets)_{t-1} < 0
    6. dLIQUID = current_ratio_t - current_ratio_{t-1} > 0
    7. EQ_OFFER: shares_outstanding_t <= shares_outstanding_{t-1}
  Operating efficiency
    8. dMARGIN = gross_margin_t - gross_margin_{t-1} > 0
    9. dTURN = asset_turnover_t - asset_turnover_{t-1} > 0
Grades (house buckets, lenient vs original 8-9 strong / 0-2 weak):
7-9 strong, 4-6 average, 0-3 weak.
Note: canonical Piotroski scales ROA/CFO by BEGINNING total assets; this
implementation uses ending-period TA (sign rarely flips, magnitudes differ).
Leverage uses ending TA (canonical uses average TA). Margin/turnover/liquidity
deltas use caller-supplied ratios; ensure they are computed consistently.
Source fields: current + prior-year statement items listed in SOURCE_FIELDS.
"""

from __future__ import annotations

from typing import Mapping

from ..common import MetricResult, get_number, unavailable

FORMULA = (
    "F = ROA>0 + CFO>0 + dROA>0 + (CFO>NI) + dLEVER<0 + dLIQUID>0 "
    "+ (shares_t<=shares_{t-1}) + dMARGIN>0 + dTURN>0 (Piotroski 2000)"
)
SOURCE_FIELDS = (
    "net_income", "total_assets", "operating_cash_flow",
    "long_term_debt", "current_ratio", "shares_outstanding",
    "gross_margin", "asset_turnover",
    "net_income_prior", "total_assets_prior",
    "long_term_debt_prior", "current_ratio_prior",
    "shares_outstanding_prior", "gross_margin_prior",
    "asset_turnover_prior",
)


def piotroski_score(fin: Mapping) -> MetricResult:
    """Compute the 9-point Piotroski F-score from current + prior-year fields."""
    missing = [k for k in SOURCE_FIELDS if get_number(fin, k) is None]
    if missing:
        return unavailable(FORMULA, list(SOURCE_FIELDS),
                           f"missing fields: {missing}")
    if get_number(fin, "total_assets") == 0 or get_number(fin, "total_assets_prior") == 0:
        return unavailable(FORMULA, list(SOURCE_FIELDS),
                           "total_assets must be non-zero in both periods")

    ni, ta = get_number(fin, "net_income"), get_number(fin, "total_assets")
    ni_p, ta_p = get_number(fin, "net_income_prior"), get_number(fin, "total_assets_prior")
    cfo = get_number(fin, "operating_cash_flow")
    ltd, ltd_p = get_number(fin, "long_term_debt"), get_number(fin, "long_term_debt_prior")

    roa, roa_p = ni / ta, ni_p / ta_p
    breakdown = {
        "roa_positive": bool(roa > 0),
        "cfo_positive": bool(cfo > 0),
        "roa_improved": bool((roa - roa_p) > 0),
        "accruals_ok": bool(cfo > ni),
        "leverage_down": bool((ltd / ta - ltd_p / ta_p) < 0),
        "liquidity_up": bool(
            (get_number(fin, "current_ratio") - get_number(fin, "current_ratio_prior")) > 0),
        "no_dilution": bool(
            get_number(fin, "shares_outstanding")
            <= get_number(fin, "shares_outstanding_prior")),
        "margin_up": bool(
            (get_number(fin, "gross_margin") - get_number(fin, "gross_margin_prior")) > 0),
        "turnover_up": bool(
            (get_number(fin, "asset_turnover") - get_number(fin, "asset_turnover_prior")) > 0),
    }
    score = sum(1 for passed in breakdown.values() if passed)
    grade = "strong" if score >= 7 else ("average" if score >= 4 else "weak")
    return MetricResult(
        {"score": score, "max": 9, "grade": grade, "breakdown": breakdown},
        FORMULA, SOURCE_FIELDS, "ok",
    )


__all__ = ["piotroski_score", "FORMULA", "SOURCE_FIELDS"]
