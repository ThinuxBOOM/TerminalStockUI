"""Deterministic fundamental ratios (Milestone 2).

Inputs are statement mappings (dict-like) with canonical field names, e.g.
revenue, revenue_prior, gross_profit, operating_income, net_income,
net_income_prior, ebit, interest_expense, total_debt, total_assets,
total_equity, total_equity_prior, invested_capital, tax_rate.

Missing fields -> "unavailable" naming the missing fields. Zero
denominators -> "unavailable" with a reason. Fallbacks (average vs ending
balance) are reported as "degraded" with an explanatory reason.
Pure functions: no network, no randomness.
"""

from __future__ import annotations

from typing import Mapping

from ..common import DEGRADED, MetricResult, get_number, missing_fields, unavailable

REVENUE_GROWTH_FORMULA = "revenue_growth = (revenue - revenue_prior) / revenue_prior"
EARNINGS_GROWTH_FORMULA = "earnings_growth = (net_income - net_income_prior) / |net_income_prior|"
GROSS_MARGIN_FORMULA = "gross_margin = gross_profit / revenue"
OPERATING_MARGIN_FORMULA = "operating_margin = operating_income / revenue"
NET_MARGIN_FORMULA = "net_margin = net_income / revenue"
DEBT_TO_EQUITY_FORMULA = "debt_to_equity = total_debt / total_equity"
DEBT_TO_ASSETS_FORMULA = "debt_to_assets = total_debt / total_assets"
INTEREST_COVERAGE_FORMULA = "interest_coverage = ebit / interest_expense"
ROE_FORMULA = "roe = net_income / average(total_equity, total_equity_prior)"
ROIC_FORMULA = "roic = nopat / invested_capital; nopat = ebit * (1 - tax_rate)"


def _require(data: Mapping, keys: list[str], formula: str) -> list[str] | None:
    missing = missing_fields(data, keys)
    if missing:
        return missing
    return None


def _ratio(
    numerator: float, denominator: float, formula: str,
    sources: list[str], denom_name: str,
) -> MetricResult:
    if denominator == 0:
        return unavailable(formula, sources, f"denominator '{denom_name}' is zero")
    return MetricResult(numerator / denominator, formula, tuple(sources), "ok")


def revenue_growth(fin: Mapping) -> MetricResult:
    """Year-over-year revenue growth."""
    keys = ["revenue", "revenue_prior"]
    missing = _require(fin, keys, REVENUE_GROWTH_FORMULA)
    if missing:
        return unavailable(REVENUE_GROWTH_FORMULA, keys, f"missing fields: {missing}")
    return _ratio(
        get_number(fin, "revenue") - get_number(fin, "revenue_prior"),
        get_number(fin, "revenue_prior"),
        REVENUE_GROWTH_FORMULA, keys, "revenue_prior",
    )


def earnings_growth(fin: Mapping) -> MetricResult:
    """Year-over-year net-income growth (scaled by |prior| so sign is kept)."""
    keys = ["net_income", "net_income_prior"]
    missing = _require(fin, keys, EARNINGS_GROWTH_FORMULA)
    if missing:
        return unavailable(EARNINGS_GROWTH_FORMULA, keys, f"missing fields: {missing}")
    prior = get_number(fin, "net_income_prior")
    if prior == 0:
        return unavailable(EARNINGS_GROWTH_FORMULA, keys,
                           "denominator 'net_income_prior' is zero")
    value = (get_number(fin, "net_income") - prior) / abs(prior)
    return MetricResult(value, EARNINGS_GROWTH_FORMULA, tuple(keys), "ok")


def gross_margin(fin: Mapping) -> MetricResult:
    """Gross profit margin."""
    keys = ["gross_profit", "revenue"]
    missing = _require(fin, keys, GROSS_MARGIN_FORMULA)
    if missing:
        return unavailable(GROSS_MARGIN_FORMULA, keys, f"missing fields: {missing}")
    return _ratio(get_number(fin, "gross_profit"), get_number(fin, "revenue"),
                  GROSS_MARGIN_FORMULA, keys, "revenue")


def operating_margin(fin: Mapping) -> MetricResult:
    """Operating income margin."""
    keys = ["operating_income", "revenue"]
    missing = _require(fin, keys, OPERATING_MARGIN_FORMULA)
    if missing:
        return unavailable(OPERATING_MARGIN_FORMULA, keys, f"missing fields: {missing}")
    return _ratio(get_number(fin, "operating_income"), get_number(fin, "revenue"),
                  OPERATING_MARGIN_FORMULA, keys, "revenue")


def net_margin(fin: Mapping) -> MetricResult:
    """Net income margin."""
    keys = ["net_income", "revenue"]
    missing = _require(fin, keys, NET_MARGIN_FORMULA)
    if missing:
        return unavailable(NET_MARGIN_FORMULA, keys, f"missing fields: {missing}")
    return _ratio(get_number(fin, "net_income"), get_number(fin, "revenue"),
                  NET_MARGIN_FORMULA, keys, "revenue")


def debt_to_equity(fin: Mapping) -> MetricResult:
    """Total debt divided by total equity."""
    keys = ["total_debt", "total_equity"]
    missing = _require(fin, keys, DEBT_TO_EQUITY_FORMULA)
    if missing:
        return unavailable(DEBT_TO_EQUITY_FORMULA, keys, f"missing fields: {missing}")
    return _ratio(get_number(fin, "total_debt"), get_number(fin, "total_equity"),
                  DEBT_TO_EQUITY_FORMULA, keys, "total_equity")


def debt_to_assets(fin: Mapping) -> MetricResult:
    """Total debt divided by total assets."""
    keys = ["total_debt", "total_assets"]
    missing = _require(fin, keys, DEBT_TO_ASSETS_FORMULA)
    if missing:
        return unavailable(DEBT_TO_ASSETS_FORMULA, keys, f"missing fields: {missing}")
    return _ratio(get_number(fin, "total_debt"), get_number(fin, "total_assets"),
                  DEBT_TO_ASSETS_FORMULA, keys, "total_assets")


def interest_coverage(fin: Mapping) -> MetricResult:
    """EBIT divided by interest expense."""
    keys = ["ebit", "interest_expense"]
    missing = _require(fin, keys, INTEREST_COVERAGE_FORMULA)
    if missing:
        return unavailable(INTEREST_COVERAGE_FORMULA, keys, f"missing fields: {missing}")
    return _ratio(get_number(fin, "ebit"), get_number(fin, "interest_expense"),
                  INTEREST_COVERAGE_FORMULA, keys, "interest_expense")


def roe(fin: Mapping) -> MetricResult:
    """Return on equity using average equity; falls back to ending equity.

    Falls back to ending equity (quality "degraded") when total_equity_prior
    is missing, and reports that in the reason.
    """
    keys = ["net_income", "total_equity"]
    missing = _require(fin, keys, ROE_FORMULA)
    if missing:
        return unavailable(ROE_FORMULA, keys + ["total_equity_prior"],
                           f"missing fields: {missing}")
    equity = get_number(fin, "total_equity")
    prior = get_number(fin, "total_equity_prior")
    if prior is None:
        if equity == 0:
            return unavailable(ROE_FORMULA, keys, "denominator 'total_equity' is zero")
        return MetricResult(
            get_number(fin, "net_income") / equity, ROE_FORMULA,
            ("net_income", "total_equity"), DEGRADED,
            reason="total_equity_prior missing; used ending equity instead of average",
        )
    avg_equity = (equity + prior) / 2.0
    if avg_equity == 0:
        return unavailable(ROE_FORMULA, keys + ["total_equity_prior"],
                           "average equity is zero")
    return MetricResult(get_number(fin, "net_income") / avg_equity, ROE_FORMULA,
                        ("net_income", "total_equity", "total_equity_prior"), "ok")


def roic(fin: Mapping) -> MetricResult:
    """Return on invested capital: NOPAT / invested capital.

    Falls back to total_debt + total_equity as invested capital (quality
    "degraded") when invested_capital is missing.
    """
    base_keys = ["ebit", "tax_rate"]
    missing = _require(fin, base_keys, ROIC_FORMULA)
    if missing:
        return unavailable(ROIC_FORMULA, base_keys + ["invested_capital"],
                           f"missing fields: {missing}")
    tax = get_number(fin, "tax_rate")
    if not 0.0 <= tax <= 1.0:
        return unavailable(ROIC_FORMULA, base_keys + ["invested_capital"],
                           f"tax_rate must be in [0, 1] (decimal, not percent), got {tax}")
    capital = get_number(fin, "invested_capital")
    sources = ["ebit", "tax_rate", "invested_capital"]
    reason = None
    quality = "ok"
    if capital is None:
        debt = get_number(fin, "total_debt")
        equity = get_number(fin, "total_equity")
        if debt is None or equity is None:
            return unavailable(
                ROIC_FORMULA, sources,
                "missing fields: ['invested_capital'] and cannot derive it "
                "(need total_debt + total_equity)",
            )
        capital = debt + equity
        sources = ["ebit", "tax_rate", "total_debt", "total_equity"]
        quality = DEGRADED
        reason = "invested_capital missing; derived as total_debt + total_equity"
    if capital == 0:
        return unavailable(ROIC_FORMULA, sources, "invested capital is zero")
    nopat = get_number(fin, "ebit") * (1.0 - tax)
    return MetricResult(nopat / capital, ROIC_FORMULA, tuple(sources), quality,
                        reason=reason)


__all__ = [
    "revenue_growth", "earnings_growth", "gross_margin", "operating_margin",
    "net_margin", "debt_to_equity", "debt_to_assets", "interest_coverage",
    "roe", "roic",
]
