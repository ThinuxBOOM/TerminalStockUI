"""Measured walk-forward skill: loader, verdicts, API wiring."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.forecast import router
from backend.forecasting import measured_skill as ms
from backend.forecasting.common import FORECAST_HORIZONS
from backend.forecasting.registry import ENSEMBLE_VERSION
from backend.forecasting.service import reset_forecast_service
from backend.tests.auth_helpers import inject_admin_auth


def _row(skill, ci, skill_iso, ci_iso):
    return {"symbols": 50, "points": 1000, "skill": skill, "skill_ci95": ci,
            "skill_isotonic": skill_iso, "skill_isotonic_ci95": ci_iso}


@pytest.fixture
def fake_report(monkeypatch):
    report = {
        "model_version": ENSEMBLE_VERSION,
        "as_of": "2026-10-01",
        "params": {"universe": ["A"] * 50},
        "horizons": {
            "1": _row(-0.02, [-0.03, -0.01], -0.05, [-0.06, -0.04]),
            "7": _row(-0.01, [-0.03, 0.01], -0.01, [-0.02, 0.02]),
            "21": _row(0.02, [0.01, 0.03], 0.03, [0.02, 0.04]),
        },
    }
    monkeypatch.setattr(ms, "_report", lambda: report)
    return report


def test_shipped_report_covers_every_horizon_for_running_model():
    ms._report.cache_clear()
    table = ms.measured_skill_table()
    assert table["measured"] is True, "re-run scripts/evaluate_forecasts.py after a model change"
    for h in FORECAST_HORIZONS:
        entry = table["horizons"][str(h)]
        assert entry["shrinkage"] is not None and entry["isotonic"] is not None


def test_calibration_method_selects_the_matching_scores(fake_report):
    assert ms.measured_skill(1, "shrinkage-0.8")["skill"] == -0.02
    assert ms.measured_skill(1, None)["calibration"] == "shrinkage"
    iso = ms.measured_skill(1, "isotonic-platt")
    assert iso["skill"] == -0.05 and iso["calibration"] == "isotonic"


def test_verdict_needs_the_interval_to_exclude_zero(fake_report):
    assert ms.measured_skill(1)["verdict"] == "worse"
    assert ms.measured_skill(7)["verdict"] == "indistinguishable"
    assert ms.measured_skill(21)["verdict"] == "better"
    assert "no better than" in ms.measured_skill(7)["summary"]
    assert "worse than" in ms.measured_skill(1)["summary"]


def test_unmeasured_horizon_or_other_model_version_is_none(fake_report, monkeypatch):
    assert ms.measured_skill(14) is None
    monkeypatch.setattr(ms, "_report", lambda: {**fake_report, "model_version": "ensemble-v0"})
    assert ms.measured_skill(1) is None
    assert ms.measured_skill_table()["measured"] is False


def _client() -> TestClient:
    reset_forecast_service()
    app = FastAPI()
    app.include_router(router)
    inject_admin_auth(app)
    return TestClient(app)


def test_measured_skill_route_is_not_read_as_a_symbol(fake_report):
    body = _client().get("/api/forecast/measured-skill").json()
    assert body["measured"] is True
    assert body["horizons"]["1"]["shrinkage"]["skill"] == -0.02


def test_forecast_payload_carries_measured_skill(fake_report):
    body = _client().get("/api/forecast/AAPL", params={"horizon": 1}).json()
    skill = body["measured_skill"]
    assert skill["horizon_days"] == 1
    expected = "isotonic" if str(body.get("calibration_method", "")).startswith("isotonic") else "shrinkage"
    assert skill["calibration"] == expected
    assert "base rate" in body["disclosure"]
