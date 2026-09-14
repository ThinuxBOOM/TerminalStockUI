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
Note: calibrated on US public manufacturers; NOT valid for financials
(banks/insurers) — use Z'/Z'' variants or suppress. Pass sector="financial"
(or is_financial=True, or fin["sector"]="financial") to get an explicit
"unavailable" instead of a misleading score.
Source fields: see SOURCE_FIELDS.
"""

from __future__ import annotations

from typing import Mapping

from ..common import MetricResult, get_number, unavailable

FINANCIAL_SECTORS = frozenset({
    "financial", "financials", "bank", "banks", "banking",
    "insurance", "insurer", "insurers", "diversified-financials",
})

FORMULA = (
    "Z = 1.2*(WC/TA) + 1.4*(RE/TA) + 3.3*(EBIT/TA) "
    "+ 0.6*(MVE/TL) + 1.0*(Sales/TA) (Altman 1968, public-manufacturing)"
)
SOURCE_FIELDS = (
    "working_capital", "retained_earnings", "ebit",
    "market_value_equity", "total_liabilities", "total_assets", "revenue",
)


def _is_financial_sector(fin: Mapping, sector: str | None, is_financial: bool) -> bool:
    if is_financial:
        return True
    candidates: list[str] = []
    if sector is not None:
        candidates.append(str(sector))
    try:
        for key in ("sector", "industry", "sector_name"):
            val = fin.get(key) if isinstance(fin, Mapping) else None
            if val is not None:
                candidates.append(str(val))
    except Exception:
        pass
    for cand in candidates:
        if cand.strip().lower() in FINANCIAL_SECTORS:
            return True
    return False


def altman_z(fin: Mapping, *, sector: str | None = None, is_financial: bool = False) -> MetricResult:
    """Compute the Altman Z-score and distress zone."""
    if _is_financial_sector(fin, sector, bool(is_financial)):
        return unavailable(FORMULA, list(SOURCE_FIELDS),
                           "Altman Z (public-manufacturing) not valid for financials; "
                           "use Z'/Z'' variant or suppress")
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
