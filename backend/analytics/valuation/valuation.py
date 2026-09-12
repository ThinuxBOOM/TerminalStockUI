"""Deterministic valuation helpers (Milestone 2).

  * wacc: exact weighted-average cost of capital (fully implemented).
  * dcf_sensitivity: STUB — simplified single-stage sensitivity grid over
    discount rates x terminal growth rates. Kept intentionally naive
    (constant near-term growth, Gordon terminal value); a full multi-stage
    DCF with fade rates is out of scope for the v1 baseline.
  * peer_compare: STUB — deterministic median/rank comparison vs peers.

Pure functions: no network, no randomness. Missing data -> "unavailable".
"""

from __future__ import annotations

from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from ..common import DEGRADED, MetricResult, get_number, unavailable

WACC_FORMULA = "WACC = (E/V)*Re + (D/V)*Rd*(1-Tc); V = E + D"
DCF_STUB_FORMULA = (
    "STUB simplified DCF: PV = sum_{t=1..n} FCF0*(1+g)^t/(1+r)^t "
    "+ TV/(1+r)^n; TV = FCF_n*(1+gt)/(r-gt); cells with r <= gt are NaN"
)
PEER_COMPARE_FORMULA = (
    "STUB peer compare: peer_median = median(peers); diff = target - median; "
    "rank_asc = 1 + #{peers with value < target} (1 = lowest)"
)

WACC_SOURCES = (
    "market_value_equity", "market_value_debt",
    "cost_of_equity", "cost_of_debt", "tax_rate",
)


def wacc(inputs: Mapping) -> MetricResult:
    """Weighted-average cost of capital from market values and component costs.

    Expects keys: market_value_equity (E), market_value_debt (D),
    cost_of_equity (Re), cost_of_debt (Rd), tax_rate (Tc).
    Cost-of-equity/debt estimation (e.g. CAPM, yield + spread) happens
    upstream; this function only combines them deterministically.
    """
    required = list(WACC_SOURCES)
    missing = [k for k in required if get_number(inputs, k) is None]
    if missing:
        return unavailable(WACC_FORMULA, required, f"missing fields: {missing}")
    equity = get_number(inputs, "market_value_equity")
    debt = get_number(inputs, "market_value_debt")
    re_ = get_number(inputs, "cost_of_equity")
    rd = get_number(inputs, "cost_of_debt")
    tax = get_number(inputs, "tax_rate")
    enterprise = equity + debt
    if enterprise <= 0:
        return unavailable(WACC_FORMULA, required,
                           "market_value_equity + market_value_debt must be > 0")
    if not 0.0 <= tax <= 1.0:
        return unavailable(WACC_FORMULA, required,
                           f"tax_rate must be in [0, 1], got {tax}")
    if equity < 0 or debt < 0:
        return unavailable(WACC_FORMULA, required,
                           "market values must be non-negative")
    value = (equity / enterprise) * re_ + (debt / enterprise) * rd * (1.0 - tax)
    return MetricResult(value, WACC_FORMULA, tuple(required), "ok")


def dcf_sensitivity(
    base_fcf: float,
    short_growth: float,
    discount_rates: Sequence[float],
    terminal_growth_rates: Sequence[float],
    projection_years: int = 5,
) -> MetricResult:
    """STUB: equity-value-per-share grid over (discount rate x terminal growth).

    Simplified single-stage model (see DCF_STUB_FORMULA). Cells where the
    discount rate does not exceed terminal growth are NaN (Gordon undefined).
    """
    sources = ("base_fcf", "short_growth", "discount_rates",
               "terminal_growth_rates", "projection_years")
    try:
        fcf = float(base_fcf)
        g = float(short_growth)
        disc = [float(r) for r in discount_rates]
        term = [float(r) for r in terminal_growth_rates]
        years = int(projection_years)
    except (ValueError, TypeError):
        return unavailable(DCF_STUB_FORMULA, list(sources),
                           "inputs must be numeric (base_fcf, growth rates, years)")
    if not np.isfinite(fcf) or fcf <= 0:
        return unavailable(DCF_STUB_FORMULA, list(sources),
                           "base_fcf must be a positive finite number for this stub")
    if not disc or not term:
        return unavailable(DCF_STUB_FORMULA, list(sources),
                           "discount_rates and terminal_growth_rates must be non-empty")
    if years < 1:
        return unavailable(DCF_STUB_FORMULA, list(sources),
                           "projection_years must be >= 1")
    if any(not np.isfinite(r) or r <= 0 for r in disc):
        return unavailable(DCF_STUB_FORMULA, list(sources),
                           "discount_rates must be positive finite numbers")

    grid = pd.DataFrame(index=pd.Index(disc, name="discount_rate"),
                        columns=pd.Index(term, name="terminal_growth"),
                        dtype=float)
    near_term = np.array([fcf * (1.0 + g) ** t for t in range(1, years + 1)])
    for r in disc:
        discount_factors = np.array([(1.0 + r) ** t for t in range(1, years + 1)])
        pv_near = float(np.sum(near_term / discount_factors))
        fcf_n = float(near_term[-1])
        for gt in term:
            if r <= gt:
                grid.loc[r, gt] = np.nan
            else:
                terminal = fcf_n * (1.0 + gt) / (r - gt)
                grid.loc[r, gt] = pv_near + terminal / ((1.0 + r) ** years)
    return MetricResult(grid, DCF_STUB_FORMULA, sources, "ok")


def peer_compare(
    target: Mapping[str, float],
    peers: Sequence[Mapping[str, float]],
    metrics: Sequence[str] | None = None,
) -> MetricResult:
    """STUB: compare target multiples/ratios against a peer set.

    Returns {"metrics": {name: {target, peer_median, peer_count, diff_vs_median,
    rank_asc}}, "peers_used": n}. Metrics with no peer coverage are reported
    as unavailable entries and downgrade quality to "degraded".
    """
    formula = PEER_COMPARE_FORMULA
    if not isinstance(peers, Sequence) or len(peers) == 0:
        return unavailable(formula, ["target", "peers"],
                           "peer set is empty; cannot compare")
    names = list(metrics) if metrics else sorted(
        {k for p in peers for k in p} | set(target)
    )
    if not names:
        return unavailable(formula, ["target", "peers"], "no metrics to compare")
    out: dict[str, dict] = {}
    notes: list[str] = []
    for name in names:
        t = get_number(target, name)
        peer_vals = [get_number(p, name) for p in peers]
        peer_vals = [v for v in peer_vals if v is not None]
        if t is None:
            out[name] = {"status": "unavailable",
                         "reason": f"target metric '{name}' missing"}
            notes.append(name)
            continue
        if not peer_vals:
            out[name] = {"status": "unavailable",
                         "reason": f"no peer coverage for '{name}'"}
            notes.append(name)
            continue
        median = float(np.median(peer_vals))
        rank_asc = 1 + sum(1 for v in peer_vals if v < t)
        out[name] = {"status": "ok", "target": t, "peer_median": median,
                     "peer_count": len(peer_vals),
                     "diff_vs_median": t - median, "rank_asc": rank_asc}
    value = {"metrics": out, "peers_used": len(peers)}
    if notes:
        return MetricResult(value, formula, ("target", "peers"), DEGRADED,
                            reason=f"partial coverage; unavailable metrics: {notes}")
    return MetricResult(value, formula, ("target", "peers"), "ok")


__all__ = ["wacc", "dcf_sensitivity", "peer_compare"]
