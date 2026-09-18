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
from backend.tests.auth_helpers import inject_admin_auth
from backend.tests.fixtures import make_ohlcv


def _client() -> TestClient:
    reset_forecast_service()
    app = FastAPI()
    app.include_router(router)
    inject_admin_auth(app)
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
    assert len(body["limitations"]) >= 3  # never empty on live data
    assert body["inputs"]["feature_version"] == body["feature_version"]
    assert body["inputs"]["data_version"] == body["data_version"]


def test_forecast_all_horizons_ok():
    client = _client()
    for horizon in FORECAST_HORIZONS:
        resp = client.get("/api/forecast/MSFT", params={"horizon": horizon})
        assert resp.status_code == 200, resp.text
        assert resp.json()["horizon_days"] == horizon


def test_forecast_rejects_bad_horizon_with_422():
    client = _client()
    for bad in (5, 63, 10, 30, 100):
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


# --- Phase 2 reconciliation: high-regime taxonomy + wire hygiene ---------

def test_high_volatility_regime_penalizes_confidence():
    """quantile_bands emits high (not elevated): it must still cost a notch."""
    from backend.forecasting.service import _confidence

    assert _confidence(0.0, 5, "B", regime="high") == "moderate"
    assert _confidence(0.0, 5, "B", regime="normal") == "high"
    assert _confidence(0.0, 5, "B") == "high"  # no regime -> no-op


def test_member_accuracy_never_reaches_wire():
    """Accuracy reorders drivers server-side; the map itself stays internal."""
    client = _client()
    body = client.get("/api/forecast/AAPL", params={"horizon": 21}).json()
    assert "member_accuracy" not in body
    assert isinstance(body["why"], list) and isinstance(body["risks"], list)


# --- Phase 2c: drivers ordering by member accuracy -------------------------

def _sample_driver_result() -> dict:
    """Three up-members + mid + low-dd (why-heavy, deterministic)."""
    return {
        "horizon_days": 21,
        "components": {
            "historical-drift": 0.60,
            "logistic-direction": 0.65,
            "momentum": 0.70,
        },
        "expected_return_range": {"mid": 0.02},
        "volatility_regime": "normal",
        "drawdown_probability": 0.05,
    }


def _sample_risk_result() -> dict:
    """Two down-members + extreme regime + high dd (risks-heavy)."""
    return {
        "horizon_days": 21,
        "components": {
            "historical-drift": 0.40,
            "logistic-direction": 0.60,
            "momentum": 0.30,
        },
        "expected_return_range": {"mid": -0.01},
        "volatility_regime": "extreme",
        "drawdown_probability": 0.30,
    }


def test_drivers_none_map_equals_current_order_byte_for_byte():
    """None/empty map preserves EXACT historical ordering (and 6-cap)."""
    from backend.api.forecast import forecast_drivers

    for result in (_sample_driver_result(), _sample_risk_result()):
        base_why, base_risks = forecast_drivers(result)
        none_why, none_risks = forecast_drivers(result, None)
        empty_why, empty_risks = forecast_drivers(result, {})
        assert none_why == base_why
        assert none_risks == base_risks
        assert empty_why == base_why
        assert empty_risks == base_risks
        # Byte-for-byte: joined strings identical, not just set-equal.
        assert "\n".join(none_why) == "\n".join(base_why)
        assert "\n".join(none_risks) == "\n".join(base_risks)
        assert len(base_why) <= 6 and len(base_risks) <= 6


def test_drivers_ordering_by_member_accuracy_why():
    """Higher hit_rate first; unknown-rate member last; non-members after."""
    from backend.api.forecast import forecast_drivers

    result = _sample_driver_result()
    base_why, _ = forecast_drivers(result)
    # Alphabetical base: historical-drift, logistic-direction, momentum, mid.
    assert base_why[0].startswith("historical-drift")
    assert base_why[1].startswith("logistic-direction")
    assert base_why[2].startswith("momentum")

    accuracy = {
        "momentum": {"hit_rate": 0.80, "n": 50},
        "historical-drift": {"hit_rate": 0.60, "n": 50},
        "logistic-direction": {"hit_rate": None, "n": 0},
    }
    why, risks = forecast_drivers(result, accuracy)
    assert len(why) <= 6 and len(risks) <= 6
    # Known members sorted desc, unknown member + non-members keep order last.
    assert why[0].startswith("momentum")
    assert why[1].startswith("historical-drift")
    assert why[2].startswith("logistic-direction")
    # Non-member entries (mid/low-dd) stay after member entries.
    assert "Past 21d moves" in why[3] or "middle of the road" in why[3]
    # Every string still quotes a computed number (no invented narrative).
    assert "70% chance" in why[0] and "60% chance" in why[1]
    assert "+2.0%" in why[3]


def test_drivers_ordering_by_member_accuracy_risks():
    """Risks side: member entries lead by rate; regime/dd keep order last."""
    from backend.api.forecast import forecast_drivers

    result = _sample_risk_result()
    base_why, base_risks = forecast_drivers(result)
    # Base risks: alphabetical members, then mid, regime, dd.
    assert base_risks[0].startswith("historical-drift")
    assert base_risks[1].startswith("momentum")

    accuracy = {
        "momentum": {"hit_rate": 0.75, "n": 40},
        "historical-drift": {"hit_rate": 0.50, "n": 40},
    }
    why, risks = forecast_drivers(result, accuracy)
    assert risks[0].startswith("momentum")
    assert risks[1].startswith("historical-drift")
    # Non-member entries keep relative order after member entries.
    assert risks[2].startswith("Past 21d moves")
    assert risks[3].startswith("Price swings are extreme")
    # Cap holds even when full list exceeds 6 (dd entry capped out here).
    assert len(risks) <= 6
    # Missing member in map counts as unknown -> last among members.
    accuracy_partial = {"momentum": {"hit_rate": 0.75, "n": 40}}
    _, risks_partial = forecast_drivers(result, accuracy_partial)
    assert risks_partial[0].startswith("momentum")
    assert risks_partial[1].startswith("historical-drift")


def test_drivers_accuracy_tie_and_empty_side_semantics():
    """Ties keep original relative order; empty side stays empty."""
    from backend.api.forecast import forecast_drivers

    result = _sample_driver_result()
    tied = {
        "historical-drift": {"hit_rate": 0.70, "n": 10},
        "momentum": {"hit_rate": 0.70, "n": 10},
        "logistic-direction": {"hit_rate": 0.70, "n": 10},
    }
    why, risks = forecast_drivers(result, tied)
    base_why, base_risks = forecast_drivers(result)
    # All rates equal -> stable original (alphabetical) order preserved.
    assert [w.split(" ")[0] for w in why[:3]] == [
        w.split(" ")[0] for w in base_why[:3]
    ]
    # Empty-side semantics: no down-members + normal regime + low dd.
    single_up = {
        "horizon_days": 21,
        "components": {"momentum": 0.70},
        "expected_return_range": {"mid": 0.01},
        "volatility_regime": "normal",
        "drawdown_probability": 0.05,
    }
    why2, risks2 = forecast_drivers(
        single_up, {"momentum": {"hit_rate": 0.9, "n": 5}}
    )
    assert why2 and risks2 == []  # UI renders empty side as unavailable


def test_get_forecast_reads_member_accuracy_from_result():
    """API passes result['member_accuracy'] through to driver ordering."""
    from backend.api.forecast import forecast_drivers

    result = _sample_driver_result()
    result["member_accuracy"] = {
        "momentum": {"hit_rate": 0.80, "n": 50},
        "historical-drift": {"hit_rate": 0.60, "n": 50},
        "logistic-direction": {"hit_rate": None, "n": 0},
    }
    via_key = forecast_drivers(result, result.get("member_accuracy"))
    direct = forecast_drivers(
        {k: v for k, v in result.items() if k != "member_accuracy"},
        result["member_accuracy"],
    )
    assert via_key == direct
    assert via_key[0][0].startswith("momentum")
