"""Risk metrics (pure) and the /api/risk endpoints (fake market data)."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.deps import get_market_service
from backend.api.risk import router
from backend.risk import metrics as M
from backend.tests.auth_helpers import inject_admin_auth


def _series(n=600, vol=0.01, seed=1, drift=0.0, beta=None, market=None):
    rng = np.random.default_rng(seed)
    noise = rng.normal(drift, vol, n)
    r = beta * market + noise if beta is not None else noise
    idx = pd.bdate_range("2023-01-02", periods=n)
    return pd.Series(100 * np.exp(np.cumsum(r)), index=idx), pd.Series(r, index=idx)


def test_annualized_vol_matches_the_generating_process():
    close, _ = _series(vol=0.01)
    vol = M.annualized_vol(M.log_returns(close), 252)
    assert vol == pytest.approx(0.01 * math.sqrt(252), rel=0.12)


def test_beta_recovers_the_true_loading():
    rng = np.random.default_rng(3)
    mret = rng.normal(0, 0.01, 600)
    close, _ = _series(vol=0.005, seed=4, beta=1.5, market=mret)
    market = pd.Series(100 * np.exp(np.cumsum(mret)), index=close.index)
    out = M.beta_corr(M.log_returns(close), M.log_returns(market))
    assert out["beta"] == pytest.approx(1.5, abs=0.1)
    assert 0.8 < out["correlation"] <= 1.0


def test_drawdown_finds_peak_and_trough():
    close = pd.Series([100, 120, 90, 95, 130, 117], index=pd.bdate_range("2024-01-01", periods=6), dtype=float)
    dd = M.drawdowns(close)
    assert dd["max"] == pytest.approx(90 / 120 - 1)
    assert dd["max_start"] == "2024-01-02" and dd["max_end"] == "2024-01-03"
    assert dd["current"] == pytest.approx(117 / 130 - 1)


def test_var_is_a_positive_loss_and_cvar_is_worse():
    close, _ = _series(vol=0.02)
    out = M.var_cvar(M.log_returns(close), 1, 0.95)
    assert 0.02 < out["var"] < 0.05
    assert out["cvar"] > out["var"]


def test_short_history_says_nothing():
    close, _ = _series(n=30)
    r = M.log_returns(close)
    assert M.var_cvar(r)["var"] is None
    assert M.sharpe_sortino(r)["sharpe"] is None


def test_portfolio_contributions_sum_to_one_and_diversify():
    rng = np.random.default_rng(9)
    idx = pd.bdate_range("2023-01-02", periods=400)
    rets = pd.DataFrame(rng.normal(0, 0.01, (400, 3)), index=idx, columns=["A", "B", "C"])
    out = M.portfolio_risk(rets, {"A": 1, "B": 1, "C": 2})
    contrib = sum(h["risk_contribution"] for h in out["holdings"])
    assert contrib == pytest.approx(1.0, abs=1e-3)
    assert out["diversification_ratio"] > 1.2  # independent assets diversify
    assert [h["weight"] for h in out["holdings"]] == [0.25, 0.25, 0.5]
    assert len(out["correlation"]["matrix"]) == 3


class FakeMarket:
    def __init__(self):
        rng = np.random.default_rng(11)
        idx = pd.bdate_range("2023-01-02", periods=700)
        self.series = {}
        mret = rng.normal(0.0003, 0.01, 700)
        for sym, beta in (("SPY", 1.0), ("AAPL", 1.2), ("MSFT", 0.9)):
            r = beta * mret + (rng.normal(0, 0.008, 700) if sym != "SPY" else 0)
            self.series[sym] = pd.Series(100 * np.exp(np.cumsum(r)), index=idx)

    def get_bars(self, symbol, timeframe="1d", limit=30):
        if symbol not in self.series:
            raise ValueError(f"unknown {symbol}")
        s = self.series[symbol].iloc[-limit:]
        return {
            "symbol": symbol,
            "bars": [{"date": str(d.date()), "ts": f"{d.date()}T04:00:00+00:00", "open": v, "high": v, "low": v,
                      "close": v, "volume": 1e6} for d, v in s.items()],
            "provenance": {"source": "fake", "as_of": "2026-01-01T00:00:00Z", "delay_minutes": 0,
                           "quality_grade": "B", "fallback_used": False, "missing_fields": []},
        }


def _client():
    app = FastAPI()
    app.include_router(router)
    inject_admin_auth(app)
    fake = FakeMarket()
    app.dependency_overrides[get_market_service] = lambda: fake
    return TestClient(app)


def test_symbol_risk_endpoint():
    body = _client().get("/api/risk/AAPL").json()
    assert body["benchmark"] == "SPY"
    assert body["market"]["beta"] == pytest.approx(1.2, abs=0.15)
    assert body["vol"]["d252"] > 0 and body["var_95"]["d1"]["var"] > 0
    assert body["provenance"]["granularity"] == "1d"


def test_portfolio_endpoint_reports_unavailable_holdings():
    body = _client().post("/api/risk/portfolio", json={
        "holdings": [{"symbol": "AAPL", "weight": 60}, {"symbol": "MSFT", "weight": 40}, {"symbol": "NOPE", "weight": 10}],
    }).json()
    assert "NOPE" in body["unavailable"]
    assert {h["symbol"] for h in body["holdings"]} == {"AAPL", "MSFT"}
    assert body["beta"] == pytest.approx(1.08, abs=0.15)


def test_portfolio_rejects_extra_fields_and_empty():
    c = _client()
    assert c.post("/api/risk/portfolio", json={"holdings": [], "x": 1}).status_code == 422
    assert c.post("/api/risk/portfolio", json={"holdings": [{"symbol": "AAPL", "weight": 0}]}).status_code == 422
