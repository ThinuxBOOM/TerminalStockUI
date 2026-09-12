"""Shared synthetic fixtures (deterministic, no network).

All randomness uses fixed seeds, so identical test runs see identical data.
Price paths are already corporate-action-adjusted by construction.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

SEED = 42
FIXED_AS_OF = "2026-09-12T00:00:00Z"


def make_ohlcv(n: int = 252, seed: int = SEED) -> pd.DataFrame:
    """Deterministic synthetic OHLCV frame with a business-day index."""
    rng = np.random.RandomState(seed)
    rets = 0.0005 + 0.01 * rng.randn(n)
    close = 100.0 * np.exp(np.cumsum(rets))
    prev_close = np.concatenate([[100.0], close[:-1]])
    spread = np.abs(rng.randn(n)) * 0.3 + 0.05
    open_ = prev_close + rng.randn(n) * 0.1
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    volume = (1_000_000 + 200_000 * rng.randn(n)).clip(min=100_000).astype(int)
    dates = pd.bdate_range("2020-01-01", periods=n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low,
         "close": close, "volume": volume.astype(float)},
        index=dates,
    )


def make_statements(strong: bool = True) -> dict:
    """Synthetic current+prior statement fields for fundamentals/quality."""
    if strong:
        return {
            "revenue": 1000.0, "revenue_prior": 900.0,
            "gross_profit": 600.0, "operating_income": 300.0,
            "net_income": 200.0, "net_income_prior": 150.0,
            "ebit": 280.0, "interest_expense": 20.0,
            "tax_rate": 0.21, "total_debt": 400.0,
            "total_assets": 2000.0, "total_assets_prior": 1800.0,
            "total_equity": 1200.0, "total_equity_prior": 1000.0,
            "invested_capital": 1600.0,
            "operating_cash_flow": 260.0,
            "long_term_debt": 300.0, "long_term_debt_prior": 350.0,
            "current_ratio": 2.0, "current_ratio_prior": 1.8,
            "shares_outstanding": 100.0, "shares_outstanding_prior": 100.0,
            "gross_margin": 0.60, "gross_margin_prior": 0.55,
            "asset_turnover": 0.55, "asset_turnover_prior": 0.50,
            "working_capital": 400.0, "retained_earnings": 800.0,
            "market_value_equity": 3000.0, "total_liabilities": 800.0,
            "receivables": 120.0, "receivables_prior": 100.0,
            "cogs": 400.0, "cogs_prior": 380.0,
            "current_assets": 700.0, "current_assets_prior": 650.0,
            "ppe_net": 900.0, "ppe_net_prior": 850.0,
            "depreciation": 90.0, "depreciation_prior": 85.0,
            "sga_expense": 200.0, "sga_expense_prior": 190.0,
            "current_liabilities": 300.0, "current_liabilities_prior": 320.0,
        }
    weak = dict(make_statements(strong=True))
    weak.update({
        "net_income": -50.0, "net_income_prior": -20.0,
        "operating_cash_flow": -80.0, "ebit": -40.0,
        "retained_earnings": -100.0, "working_capital": -50.0,
        "long_term_debt": 900.0, "long_term_debt_prior": 500.0,
        "current_ratio": 0.8, "current_ratio_prior": 1.0,
        "shares_outstanding": 130.0,
        "gross_margin": 0.30, "gross_margin_prior": 0.35,
        "asset_turnover": 0.40, "asset_turnover_prior": 0.45,
    })
    return weak


def make_events() -> dict[str, list[dict]]:
    """Small unsorted event feeds with aliases, a duplicate and a bad row."""
    return {
        "earnings": [
            {"report_date": "2021-04-01", "eps_actual": 1.2, "source": "prov"},
            {"date": "2021-01-05", "eps": 1.0},
            {"date": "not-a-date", "eps": 0.5},
        ],
        "dividends": [
            {"ex_date": "2021-02-10", "dividend_amount": 0.5, "currency": "USD"},
            {"date": "2021-02-10", "dividend_amount": 0.5, "currency": "USD"},
        ],
        "splits": [
            {"date": "2021-03-01", "split_ratio": "2:1"},
        ],
    }
