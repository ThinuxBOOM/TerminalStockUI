"""Backtest API tests: TestClient, no network, deterministic + leakage guard."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.backtest import reset_backtest_history, router
from backend.forecasting.backtesting import LeakageError, assert_no_leakage


def _client() -> TestClient:
    reset_backtest_history()
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_backtest_run_shape_and_history():
    client = _client()
    resp = client.post(
        "/api/backtest/run",
        json={"symbol": "AAPL", "horizons": [5, 21],
              "train_size": 100, "test_size": 21, "gap": 21},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["symbol"] == "AAPL"
    assert body["as_of"]
    assert body["provenance"]
    assert body["model_version"] and body["feature_version"] and body["data_version"]
    assert set(body["results"]) == {"5", "21"}
    for horizon, metrics in body["results"].items():
        assert metrics["n_points"] > 0
        assert metrics["n_folds"] >= 1
        assert 0.0 <= metrics["brier"] <= 1.0
        assert 0.0 <= metrics["ece"] <= 1.0
        table = metrics["reliability"]
        assert len(table) == 10
        assert sum(row["count"] for row in table) == metrics["n_points"]
        for row in table:
            for key in ("bin_low", "bin_high", "count",
                        "mean_predicted", "fraction_positive"):
                assert key in row, (horizon, key)
    hist = client.get("/api/backtest/AAPL")
    assert hist.status_code == 200, hist.text
    hbody = hist.json()
    assert hbody["symbol"] == "AAPL"
    assert hbody["provenance"]
    assert len(hbody["runs"]) == 1
    summary = hbody["runs"][0]
    assert summary["metrics"]["5"]["brier"] == pytest.approx(body["results"]["5"]["brier"])
    assert "reliability" not in summary["metrics"]["5"]  # lightweight


def test_backtest_rejects_bad_horizon_with_422():
    client = _client()
    resp = client.post("/api/backtest/run", json={"symbol": "AAPL", "horizons": [10]})
    assert resp.status_code == 422, resp.text
    resp = client.post("/api/backtest/run", json={"symbol": "AAPL", "horizons": []})
    assert resp.status_code == 422, resp.text


def test_backtest_rejects_leaky_gap_with_422():
    # gap < max(horizons) would let train labels straddle tests.
    client = _client()
    resp = client.post(
        "/api/backtest/run",
        json={"symbol": "AAPL", "horizons": [5, 21],
              "train_size": 100, "test_size": 21, "gap": 5},
    )
    assert resp.status_code == 422, resp.text
    assert "gap" in resp.text.lower()


def test_backtest_deterministic():
    client = _client()
    payload = {"symbol": "MSFT", "horizons": [5],
               "train_size": 100, "test_size": 21, "gap": 5}
    first = client.post("/api/backtest/run", json=payload).json()
    reset_backtest_history()
    # Rebuild a fresh client without clearing the deterministic bars seed.
    second = client.post("/api/backtest/run", json=payload).json()
    assert first["results"]["5"]["brier"] == pytest.approx(second["results"]["5"]["brier"])
    assert first["results"]["5"]["ece"] == pytest.approx(second["results"]["5"]["ece"])


def test_backtest_leakage_guard():
    with pytest.raises(LeakageError):
        assert_no_leakage([0, 1, 2], [2, 3, 4])  # overlap
    with pytest.raises(LeakageError):
        assert_no_leakage([0, 1, 2], [3, 4], gap=2)  # gap violated
    assert_no_leakage([0, 1, 2], [5, 6], gap=2)
