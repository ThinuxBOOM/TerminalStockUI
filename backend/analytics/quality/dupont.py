"""DuPont 3-step decomposition: ROE = net_margin * asset_turnover * equity_multiplier.

Formula: ROE = (net_income/revenue) * (revenue/avg_assets)
               * (avg_assets/avg_equity).
Averages use (current + prior)/2; when a prior balance is missing the ending
balance is used instead and quality is "degraded" with a reason.
Source fields: revenue, net_income, total_assets, total_assets_prior,
total_equity, total_equity_prior.
"""

from __future__ import annotations

from typing import Mapping

from ..common import DEGRADED, MetricResult, get_number, unavailable

FORMULA = (
    "ROE = (NI/revenue) * (revenue/avg_assets) * (avg_assets/avg_equity); "
    "averages are (current+prior)/2, else ending balance (degraded)"
)
SOURCE_FIELDS = (
    "revenue", "net_income", "total_assets",
    "total_assets_prior", "total_equity", "total_equity_prior",
)


def dupont(fin: Mapping) -> MetricResult:
    """Decompose ROE into margin, turnover and leverage components."""
    required = ["revenue", "net_income", "total_assets", "total_equity"]
    missing = [k for k in required if get_number(fin, k) is None]
    if missing:
        return unavailable(FORMULA, list(SOURCE_FIELDS),
                           f"missing fields: {missing}")
    revenue = get_number(fin, "revenue")
    if revenue == 0:
        return unavailable(FORMULA, list(SOURCE_FIELDS),
                           "denominator 'revenue' is zero")
    assets, assets_p = get_number(fin, "total_assets"), get_number(fin, "total_assets_prior")
    equity, equity_p = get_number(fin, "total_equity"), get_number(fin, "total_equity_prior")
    notes: list[str] = []
    avg_assets = (assets + assets_p) / 2.0 if assets_p is not None else assets
    avg_equity = (equity + equity_p) / 2.0 if equity_p is not None else equity
    if assets_p is None:
        notes.append("total_assets_prior missing; used ending assets")
    if equity_p is None:
        notes.append("total_equity_prior missing; used ending equity")
    if avg_assets == 0:
        return unavailable(FORMULA, list(SOURCE_FIELDS), "average assets is zero")
    if avg_equity == 0:
        return unavailable(FORMULA, list(SOURCE_FIELDS), "average equity is zero")
    net_margin = get_number(fin, "net_income") / revenue
    turnover = revenue / avg_assets
    multiplier = avg_assets / avg_equity
    value = {
        "roe": net_margin * turnover * multiplier,
        "net_margin": net_margin,
        "asset_turnover": turnover,
        "equity_multiplier": multiplier,
        "avg_assets": avg_assets,
        "avg_equity": avg_equity,
    }
    if avg_equity < 0:
        notes.append("average equity negative; ROE sign-flipped, interpret with caution")
    if notes:
        return MetricResult(value, FORMULA, SOURCE_FIELDS, DEGRADED,
                            reason="; ".join(notes))
    return MetricResult(value, FORMULA, SOURCE_FIELDS, "ok")


__all__ = ["dupont", "FORMULA", "SOURCE_FIELDS"]
