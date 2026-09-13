"""Forecast API tests: TestClient, no network, deterministic + leakage guard."""

from __future__ import annotations

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.forecast import router
from backend.forecasting.backtesting import LeakageError, assert_no_leakage
from backend.forecasting.common import FORECAST_HORIZONS
from backend.forecasting.features import build_features
from backend.forecasting.service import reset_forecast_service
from backend.tests.fixtures import make_ohlcv


def _client() -> TestClient:
    reset_forecast_service()
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_forecast_shape_and_provenance():
    client = _client()
    resp = client.get("/api/forecast/AAPL", params={"horizon": 21})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["symbol"] == "AAPL"
    assert body["horizon_days"] == 21
    assert 0.0 <= body["direction_probability"] <= 1.0
    rng = body["expected_return_range"]
    assert rng["low"] <= rng["mid"] <= rng["high"]
    assert body["volatility_regime"] in ("low", "normal", "high")
    assert 0.0 <= body["drawdown_probability"] <= 1.0
    assert body["confidence"] in ("low", "moderate", "high")
    assert body["model_version"]
    assert body["feature_version"]
    assert body["data_version"]
    assert body["as_of"]
    assert body["disclosure"] == "Not investment advice"
    prov = body["provenance"]
    for key in ("source", "as_of", "delay_minutes", "quality_grade",
                "fallback_used", "missing_fields"):
        assert key in prov, f"provenance missing {key}"
    assert set(body["components"]) >= {"historical-drift", "momentum"}


def test_forecast_display_fields_for_terminal_ui():
    """Display contract: label/quality/provider/why/risks/evidence_ids.

    Regression for the empty-label / quality-U / unavailable-drivers panel:
    every field the Security Brief renders must be present and derived.
    """
    from backend.api.forecast import direction_label

    assert direction_label(0.64) == "moderately positive"
    assert direction_label(0.36) == "moderately negative"
    assert direction_label(0.5) == "neutral"
    assert direction_label(0.80) == "clearly positive"
    client = _client()
    body = client.get("/api/forecast/AAPL", params={"horizon": 21}).json()
    assert body["label"] in (
        "clearly positive", "moderately positive", "slightly positive",
        "neutral", "slightly negative", "moderately negative",
        "clearly negative",
    )
    assert body["quality_grade"] == body["provenance"]["quality_grade"]
    assert body["provider"] == "deterministic-engine"
    assert isinstance(body["why"], list) and isinstance(body["risks"], list)
    assert len(body["evidence_ids"]) >= 2
    assert all(isinstance(e, str) and e for e in body["evidence_ids"])
    assert any("momentum" in e for e in body["evidence_ids"])


def test_forecast_all_horizons_ok():
    client = _client()
    for horizon in FORECAST_HORIZONS:
        resp = client.get("/api/forecast/MSFT", params={"horizon": horizon})
        assert resp.status_code == 200, resp.text
        assert resp.json()["horizon_days"] == horizon


def test_forecast_rejects_bad_horizon_with_422():
    client = _client()
    for bad in (1, 7, 10, 30, 63 * 2):
        resp = client.get("/api/forecast/AAPL", params={"horizon": bad})
        assert resp.status_code == 422, (bad, resp.text)


def test_forecast_deterministic_across_calls():
    client = _client()
    first = client.get("/api/forecast/AAPL", params={"horizon": 21}).json()
    second = client.get("/api/forecast/AAPL", params={"horizon": 21}).json()
    assert first["direction_probability"] == pytest.approx(second["direction_probability"])
    assert first["expected_return_range"] == pytest.approx(second["expected_return_range"])
    assert first["model_version"] == second["model_version"]
    assert first["feature_version"] == second["feature_version"]


def test_forecast_leakage_guard():
    # Splitter guard: overlap / gap violations raise; past-only features hold.
    with pytest.raises(LeakageError):
        assert_no_leakage([0, 1, 2], [2, 3, 4])
    with pytest.raises(LeakageError):
        assert_no_leakage([0, 1, 2], [3, 4], gap=2)
    assert_no_leakage([0, 1, 2], [5, 6], gap=2)  # exactly satisfied: OK
    ohlcv = make_ohlcv()
    short = build_features(ohlcv.iloc[:100])
    long_ = build_features(ohlcv.iloc[:150])
    pd.testing.assert_frame_equal(short, long_.loc[short.index])
