"""Beneish M-score (Beneish, 1999, "The Detection of Earnings Manipulation").

Formula: M = -4.84 + 0.92*DSRI + 0.528*GMI + 0.404*AQI + 0.892*SGI
               + 0.115*DEPI - 0.172*SGAI + 4.679*TATA - 0.327*LVGI
  DSRI = (receivables_t/revenue_t) / (receivables_{t-1}/revenue_{t-1})
  GMI  = margin_{t-1} / margin_t, margin = (revenue - cogs)/revenue
  AQI  = [1-(current_assets_t+ppe_net_t)/total_assets_t]
         / [1-(current_assets_{t-1}+ppe_net_{t-1})/total_assets_{t-1}]
  SGI  = revenue_t / revenue_{t-1}
  DEPI = [depr_{t-1}/(depr_{t-1}+ppe_{t-1})] / [depr_t/(depr_t+ppe_t)]
  SGAI = (sga_t/revenue_t) / (sga_{t-1}/revenue_{t-1})
  LVGI = [(current_liab_t+ltd_t)/TA_t] / [(current_liab_{t-1}+ltd_{t-1})/TA_{t-1}]
  TATA = (net_income_t - operating_cash_flow_t) / total_assets_t
Flag: M > -1.78 -> "likely manipulator" (original cutoff).
Any zero denominator in the eight indices -> "unavailable" naming it.
Source fields: see SOURCE_FIELDS (uses net_income as the earnings input
to TATA; documented proxy for income from continuing operations).
"""

from __future__ import annotations

from typing import Mapping

from ..common import MetricResult, get_number, unavailable

FORMULA = (
    "M = -4.84 + 0.92*DSRI + 0.528*GMI + 0.404*AQI + 0.892*SGI "
    "+ 0.115*DEPI - 0.172*SGAI + 4.679*TATA - 0.327*LVGI; "
    "likely manipulator if M > -1.78 (Beneish 1999)"
)
SOURCE_FIELDS = (
    "receivables", "revenue", "cogs", "total_assets",
    "current_assets", "ppe_net", "depreciation",
    "sga_expense", "current_liabilities", "long_term_debt",
    "net_income", "operating_cash_flow",
    "receivables_prior", "revenue_prior", "cogs_prior",
    "total_assets_prior", "current_assets_prior", "ppe_net_prior",
    "depreciation_prior", "sga_expense_prior",
    "current_liabilities_prior", "long_term_debt_prior",
)
CUTOFF = -1.78


def _ratio(num: float, den: float, name: str) -> tuple[float | None, str | None]:
    if den == 0:
        return None, f"zero denominator in {name}"
    return num / den, None


def beneish_m(fin: Mapping) -> MetricResult:
    """Compute the Beneish M-score and manipulator flag."""
    missing = [k for k in SOURCE_FIELDS if get_number(fin, k) is None]
    if missing:
        return unavailable(FORMULA, list(SOURCE_FIELDS),
                           f"missing fields: {missing}")
    g = lambda k: get_number(fin, k)  # noqa: E731
    errors: list[str] = []

    def idx(value: float | None, err: str | None) -> float:
        if err is not None:
            errors.append(err)
            return float("nan")
        return value  # type: ignore[return-value]

    r_t, e = _ratio(g("receivables"), g("revenue"), "DSRI numerator")
    r_p, e2 = _ratio(g("receivables_prior"), g("revenue_prior"), "DSRI denominator")
    dsri, e3 = (None, e or e2) if (e or e2) else _ratio(r_t, r_p, "DSRI")
    DSRI = idx(*((dsri, e3) if e3 else (dsri, None)))

    m_t = (g("revenue") - g("cogs")) / g("revenue") if g("revenue") != 0 else None
    m_p = (g("revenue_prior") - g("cogs_prior")) / g("revenue_prior") \
        if g("revenue_prior") != 0 else None
    if m_t is None or m_p is None:
        GMI, g_err = None, "zero revenue in GMI"
    else:
        GMI, g_err = _ratio(m_p, m_t, "GMI")
    GMI = idx(GMI, g_err)

    a_t = 1.0 - (g("current_assets") + g("ppe_net")) / g("total_assets") \
        if g("total_assets") != 0 else None
    a_p = 1.0 - (g("current_assets_prior") + g("ppe_net_prior")) / g("total_assets_prior") \
        if g("total_assets_prior") != 0 else None
    if a_t is None or a_p is None:
        AQI, a_err = None, "zero total_assets in AQI"
    else:
        AQI, a_err = _ratio(a_t, a_p, "AQI")
    AQI = idx(AQI, a_err)

    SGI, s_err = _ratio(g("revenue"), g("revenue_prior"), "SGI")
    SGI = idx(SGI, s_err)

    d_t_den = g("depreciation") + g("ppe_net")
    d_p_den = g("depreciation_prior") + g("ppe_net_prior")
    d_t = g("depreciation") / d_t_den if d_t_den != 0 else None
    d_p = g("depreciation_prior") / d_p_den if d_p_den != 0 else None
    if d_t is None or d_p is None:
        DEPI, d_err = None, "zero (depreciation + ppe_net) in DEPI"
    else:
        DEPI, d_err = _ratio(d_p, d_t, "DEPI")
    DEPI = idx(DEPI, d_err)

    sga_t, se = _ratio(g("sga_expense"), g("revenue"), "SGAI numerator")
    sga_p, se2 = _ratio(g("sga_expense_prior"), g("revenue_prior"), "SGAI denominator")
    sgai, se3 = (None, se or se2) if (se or se2) else _ratio(sga_t, sga_p, "SGAI")
    SGAI = idx(*((sgai, se3) if se3 else (sgai, None)))

    l_t_den, l_p_den = g("total_assets"), g("total_assets_prior")
    l_t = (g("current_liabilities") + g("long_term_debt")) / l_t_den if l_t_den != 0 else None
    l_p = (g("current_liabilities_prior") + g("long_term_debt_prior")) / l_p_den \
        if l_p_den != 0 else None
    if l_t is None or l_p is None:
        LVGI, l_err = None, "zero total_assets in LVGI"
    else:
        LVGI, l_err = _ratio(l_t, l_p, "LVGI")
    LVGI = idx(LVGI, l_err)

    TATA = (g("net_income") - g("operating_cash_flow")) / g("total_assets") \
        if g("total_assets") != 0 else idx(None, "zero total_assets in TATA")

    if errors:
        return unavailable(FORMULA, list(SOURCE_FIELDS), "; ".join(errors))
    components = {"DSRI": DSRI, "GMI": GMI, "AQI": AQI, "SGI": SGI,
                  "DEPI": DEPI, "SGAI": SGAI, "LVGI": LVGI, "TATA": TATA}
    m = (-4.84 + 0.92 * DSRI + 0.528 * GMI + 0.404 * AQI + 0.892 * SGI
         + 0.115 * DEPI - 0.172 * SGAI + 4.679 * TATA - 0.327 * LVGI)
    return MetricResult(
        {"m": m, "likely_manipulator": bool(m > CUTOFF),
         "cutoff": CUTOFF, "components": components},
        FORMULA, SOURCE_FIELDS, "ok",
    )


__all__ = ["beneish_m", "FORMULA", "SOURCE_FIELDS", "CUTOFF"]
