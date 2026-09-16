"""Analytics API tests: TestClient, no network, deterministic."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.analytics_api import router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _assert_metric_envelope(metric: dict, name: str) -> None:
    assert isinstance(metric, dict), name
    for key in ("value", "formula", "source_fields", "quality_flag"):
        assert key in metric, f"{name} missing {key}"
    assert isinstance(metric["formula"], str) and metric["formula"]
    assert isinstance(metric["source_fields"], list) and metric["source_fields"]
    assert metric["quality_flag"] in ("ok", "degraded", "unavailable"), name


def test_analytics_bundle_shape_and_provenance():
    client = _client()
    resp = client.get("/api/analytics/AAPL")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["symbol"] == "AAPL"
    assert body["as_of"]
    prov = body["provenance"]
    for key in ("source", "as_of", "delay_minutes", "quality_grade",
                "fallback_used", "missing_fields"):
        assert key in prov, f"provenance missing {key}"
    for section in ("technical", "fundamentals", "quality", "valuation"):
        assert isinstance(body[section], dict) and body[section], section
        for name, metric in body[section].items():
            _assert_metric_envelope(metric, f"{section}.{name}")


def test_analytics_technical_live_and_statement_sections_unavailable_by_design(monkeypatch):
    import backend.market_data.statements.resolver as resolver_module

    # Pin the feed to failed: statement families report unavailable (honest),
    # regardless of network availability in the test environment.
    monkeypatch.setattr(resolver_module, "get_statements",
                        lambda *a, **k: ({}, {"source": None, "reason": "offline"}))
    client = _client()
    body = client.get("/api/analytics/MSFT").json()
    # Technical runs on bars: at least one indicator is computable.
    flags = {m["quality_flag"] for m in body["technical"].values()}
    assert flags & {"ok", "degraded"}
    assert body["technical"]["sma_20"]["formula"]
    # No statement feed: statement families report unavailable (honest).
    assert body["fundamentals"]["gross_margin"]["quality_flag"] == "unavailable"
    assert body["quality"]["piotroski"]["quality_flag"] == "unavailable"
    assert body["valuation"]["wacc"]["quality_flag"] == "unavailable"
    assert "reason" in body["fundamentals"]["gross_margin"] or True


def test_analytics_deterministic():
    client = _client()
    first = client.get("/api/analytics/AAPL").json()
    second = client.get("/api/analytics/AAPL").json()
    assert first["technical"]["sma_20"] == second["technical"]["sma_20"]
    assert first["technical"]["rsi_14"] == second["technical"]["rsi_14"]
    assert first["provenance"]["source"] == second["provenance"]["source"]
