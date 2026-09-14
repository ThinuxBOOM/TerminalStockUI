"""Phase 2b calibration snapshot tests (offline only, no network).

Covers: snapshot math on fixture bars, venue member versions, upsert
idempotency on sqlite, cron auth reuse + never-500 batch, forecast endpoint
calibration rows (present vs []), zero-window empty history, bad inputs,
and the migration/model contract (verified on sqlite via init_db).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.api.deps import reset_deps
from backend.db.models import Base, CalibrationSnapshot
from backend.db.session import get_session_factory, init_db, reset_engine
from backend.forecasting.calibration.snapshots import (
    build_snapshot,
    get_latest_snapshot,
    upsert_snapshot,
)
from backend.forecasting.common import FORECAST_HORIZONS
from backend.forecasting.service import reset_forecast_service
from backend.instruments.registry import InstrumentRegistry
from backend.tests.fixtures import make_ohlcv

PROVENANCE_KEYS = {
    "source", "as_of", "delay_minutes", "quality_grade",
    "fallback_used", "missing_fields",
}

RELIABILITY_KEYS = {"bin_low", "bin_high", "count", "mean_predicted", "fraction_positive"}
US_MEMBERS = {"historical-drift", "momentum", "logistic-direction"}


class FakeMarket:
    """Deterministic bars from a fixture frame (no network)."""

    def __init__(self, frame=None, provenance=None):
        self.registry = InstrumentRegistry()
        self._frame = make_ohlcv(252) if frame is None else frame
        self._provenance = provenance or {
            "source": "fake", "as_of": "2026-09-12T00:00:00Z",
            "delay_minutes": 15, "quality_grade": "B",
            "fallback_used": False, "missing_fields": [],
        }

    def get_bars(self, symbol, timeframe="1d", limit=250):
        n = max(1, min(int(limit), 250))
        frame = self._frame.iloc[-n:]
        instrument, _, _ = self.registry.resolve((symbol or "").strip().upper())
        bars = [
            {
                "ts": ts.isoformat(),
                "open": float(row["open"]), "high": float(row["high"]),
                "low": float(row["low"]), "close": float(row["close"]),
                "volume": float(row["volume"]),
            }
            for ts, row in frame.iterrows()
        ]
        return {
            "symbol": (symbol or "").strip().upper(),
            "instrument_id": instrument.instrument_id if instrument else None,
            "timeframe": timeframe,
            "bars": bars,
            "provenance": dict(self._provenance),
        }


class EmptyMarket(FakeMarket):
    """No bars at all (thin/unknown history path)."""

    def get_bars(self, symbol, timeframe="1d", limit=250):
        out = super().get_bars(symbol, timeframe, limit)
        out["bars"] = []
        return out


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path}/calib.db"
    monkeypatch.setenv("DATABASE_URL", url)
    reset_engine()
    init_db(url)
    try:
        yield url
    finally:
        reset_engine()


def _teardown() -> None:
    reset_deps()
    reset_engine()
    reset_forecast_service()


def _client() -> TestClient:
    reset_deps()
    reset_forecast_service()
    return TestClient(create_app())


# --- snapshot math ------------------------------------------------------------


def test_snapshot_math_on_fixture_bars():
    market = FakeMarket()
    first = build_snapshot("AAPL", 21, market_service=market)
    assert first["symbol"] == "AAPL"
    assert first["exchange_mic"] == "XNAS"
    assert first["horizon_days"] == 21
    assert first["model_version"] == "ensemble-v1"
    assert first["feature_version"]
    assert first["data_version"]
    assert first["n_windows"] > 0
    assert 0.0 <= first["brier"] <= 1.0
    assert 0.0 <= first["ece"] <= 1.0
    assert len(first["reliability"]) == 10
    for row in first["reliability"]:
        assert RELIABILITY_KEYS <= set(row)
        assert row["bin_low"] < row["bin_high"]
        assert row["count"] >= 0
    assert sum(r["count"] for r in first["reliability"]) == first["n_windows"]
    assert set(first["members"]) == US_MEMBERS  # keys match the US ensemble
    for name, entry in first["members"].items():
        assert set(entry) == {"hit_rate", "n"}
        assert entry["n"] > 0
        assert entry["hit_rate"] is None or 0.0 <= entry["hit_rate"] <= 1.0
    second = build_snapshot("AAPL", 21, market_service=market)
    assert second == first  # deterministic: same bars -> same snapshot


def test_snapshot_all_horizons_ok():
    market = FakeMarket()
    for horizon in FORECAST_HORIZONS:
        snap = build_snapshot("MSFT", horizon, market_service=market)
        assert snap["horizon_days"] == horizon
        assert snap["n_windows"] >= 0
        assert sum(r["count"] for r in snap["reliability"]) == snap["n_windows"]


def test_snapshot_venue_member_versions():
    market = FakeMarket()
    sse = build_snapshot("600519.SS", 21, market_service=market)
    assert sse["symbol"] == "600519" and sse["exchange_mic"] == "XSHG"
    assert "sse-drift" in sse["model_version"]
    assert set(sse["members"]) == US_MEMBERS | {"sse-drift"}
    eux = build_snapshot("MC.PA", 21, market_service=market)
    assert eux["symbol"] == "MC" and eux["exchange_mic"] == "XPAR"
    assert "eux-drift" in eux["model_version"]
    assert set(eux["members"]) == US_MEMBERS | {"eux-drift"}


def test_snapshot_bad_inputs_raise():
    market = FakeMarket()
    with pytest.raises(ValueError):
        build_snapshot("AAPL", 7, market_service=market)
    with pytest.raises(ValueError):
        build_snapshot("   ", 21, market_service=market)


def test_empty_history_yields_zero_window_not_crash():
    snap = build_snapshot("AAPL", 21, market_service=EmptyMarket())
    assert snap["n_windows"] == 0
    assert snap["brier"] is None and snap["ece"] is None
    assert snap["reliability"] == []
    assert set(snap["members"]) == US_MEMBERS
    assert all(v["hit_rate"] is None and v["n"] == 0 for v in snap["members"].values())
    assert snap["model_version"] and snap["feature_version"] and snap["data_version"]


# --- sqlite persistence -------------------------------------------------------


def test_upsert_idempotency_on_sqlite(isolated_db):
    from sqlalchemy import func, select

    market = FakeMarket()
    snap = build_snapshot("AAPL", 21, market_service=market)
    Session = get_session_factory()
    db = Session()
    try:
        first = upsert_snapshot(db, snap)
        second = upsert_snapshot(db, snap)
        assert str(first.snapshot_id) == str(second.snapshot_id)
        count = db.execute(
            select(func.count()).select_from(CalibrationSnapshot)).scalar()
        assert count == 1
        got = get_latest_snapshot(db, "AAPL", 21, snap["model_version"])
        assert got is not None
        assert got.n_windows == snap["n_windows"]
        assert list(got.reliability) == snap["reliability"]
        assert get_latest_snapshot(db, "NOPE", 21, "ensemble-v1") is None
    finally:
        db.close()
        _teardown()


def test_zero_window_snapshot_persists(isolated_db):
    snap = build_snapshot("AAPL", 5, market_service=EmptyMarket())
    Session = get_session_factory()
    db = Session()
    try:
        row = upsert_snapshot(db, snap)
        assert row.n_windows == 0 and row.brier is None
        got = get_latest_snapshot(db, "AAPL", 5, snap["model_version"])
        assert got is not None and got.reliability == []
    finally:
        db.close()
        _teardown()


def test_model_and_migration_contract(isolated_db):
    from pathlib import Path

    assert "calibration_snapshots" in set(Base.metadata.tables)
    cols = {c.name for c in Base.metadata.tables["calibration_snapshots"].columns}
    assert {"snapshot_id", "model_version", "feature_version", "horizon_days",
            "symbol", "exchange_mic", "brier", "ece", "n_windows",
            "reliability", "members", "data_version", "created_at"} <= cols
    root = Path(__file__).resolve().parents[2]
    infra = (root / "infra" / "migrations" / "0002_calibration.sql").read_text()
    supa = (root / "supabase" / "migrations" / "0002_calibration.sql").read_text()
    for text in (infra, supa):
        lowered = text.lower()
        assert "create table if not exists calibration_snapshots" in lowered
        assert "gen_random_uuid()" in lowered
        assert "check (horizon_days in (5, 21, 63))" in lowered
        assert "uq_calibration_snapshot" in lowered
        assert "unique (symbol, horizon_days, model_version, feature_version, data_version)" in lowered
        assert "reliability" in lowered and "members" in lowered
        assert "ix_calibration_snapshots_symbol_horizon" in lowered
    assert "enable row level security" in supa.lower()
    # sqlite round-trip via init_db already covered by isolated_db + upserts


# --- cron ---------------------------------------------------------------------


def test_cron_calibrate_auth_reuse_401(isolated_db, monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "s3cr3t")
    client = _client()
    try:
        assert client.get("/api/cron/calibrate").status_code == 401
        resp = client.get("/api/cron/calibrate",
                          headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 401
        assert client.post("/api/cron/calibrate",
                           json={"symbols": ["AAPL"]}).status_code == 401
    finally:
        _teardown()


def test_cron_calibrate_single_symbol_200(isolated_db, monkeypatch):
    monkeypatch.delenv("CRON_SECRET", raising=False)
    client = _client()
    try:
        resp = client.get("/api/cron/calibrate", params={"symbol": "AAPL"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        # Honest counting: calibrated = n>=10 only (h=63 stub n=1 is weak,
        # not skill). calibrated + unscored covers all horizons.
        assert body["calibrated"] + body["unscored"] == len(FORECAST_HORIZONS)
        assert set(body["snapshots"]) == {f"AAPL:{h}" for h in FORECAST_HORIZONS}
        assert all(n >= 0 for n in body["snapshots"].values())
        assert body["errors"] == {}
        assert PROVENANCE_KEYS <= set(body["provenance"])
    finally:
        _teardown()


def test_cron_calibrate_post_never_500s_batch(isolated_db, monkeypatch):
    monkeypatch.delenv("CRON_SECRET", raising=False)
    client = _client()
    try:
        resp = client.post("/api/cron/calibrate",
                           json={"symbols": ["AAPL", "ZZZ_NOPE_123"]})
        assert resp.status_code == 200, resp.text  # stub bars cover unknown too
        body = resp.json()
        assert body["calibrated"] + body["unscored"] == 2 * len(FORECAST_HORIZONS)
        assert body["errors"] == {}
    finally:
        _teardown()


def test_cron_calibrate_per_pair_errors_never_500(isolated_db, monkeypatch):
    import backend.forecasting.calibration.snapshots as snap_module

    monkeypatch.delenv("CRON_SECRET", raising=False)
    real_build = snap_module.build_snapshot

    def _flaky(symbol, horizon, **kwargs):
        if (symbol or "").strip().upper() == "BOOM":
            raise RuntimeError("boom")
        return real_build(symbol, horizon, **kwargs)

    monkeypatch.setattr(snap_module, "build_snapshot", _flaky)
    client = _client()
    try:
        resp = client.post("/api/cron/calibrate",
                           json={"symbols": ["AAPL", "BOOM"]})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is False
        assert body["calibrated"] + body["unscored"] == len(FORECAST_HORIZONS)
        assert set(body["snapshots"]) == {f"AAPL:{h}" for h in FORECAST_HORIZONS}
        assert set(body["errors"]) == {f"BOOM:{h}" for h in FORECAST_HORIZONS}
        assert PROVENANCE_KEYS <= set(body["provenance"])
    finally:
        _teardown()


# --- forecast endpoint --------------------------------------------------------


def test_forecast_calibration_empty_then_present(isolated_db, monkeypatch):
    monkeypatch.delenv("CRON_SECRET", raising=False)
    client = _client()
    try:
        resp = client.get("/api/forecast/AAPL", params={"horizon": 21})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["calibration"] == []  # no snapshot yet
        model_version = body["model_version"]

        snap = build_snapshot("AAPL", 21, market_service=FakeMarket())
        assert snap["model_version"] == model_version  # join key matches live
        Session = get_session_factory()
        db = Session()
        try:
            upsert_snapshot(db, snap)
        finally:
            db.close()

        again = client.get("/api/forecast/AAPL", params={"horizon": 21}).json()
        assert again["calibration"] == snap["reliability"]
        assert len(again["calibration"]) == 10
        # Other horizons still empty (lookup is per-horizon).
        other = client.get("/api/forecast/AAPL", params={"horizon": 5}).json()
        assert other["calibration"] == []
    finally:
        _teardown()


def test_forecast_calibration_db_issue_still_200(isolated_db, monkeypatch):
    import backend.db.session as session_module

    client = _client()
    try:
        resp = client.get("/api/forecast/AAPL", params={"horizon": 21})
        assert resp.status_code == 200 and resp.json()["calibration"] == []

        def _boom(*args, **kwargs):
            raise RuntimeError("db down")

        monkeypatch.setattr(session_module, "get_session_factory", _boom)
        resp = client.get("/api/forecast/AAPL", params={"horizon": 21})
        assert resp.status_code == 200, resp.text
        assert resp.json()["calibration"] == []
    finally:
        _teardown()
