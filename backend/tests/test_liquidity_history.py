"""GET /api/markets/{mic}/liquidity/history tests: TestClient, isolated sqlite, no network."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import deps as deps_module
from backend.api.deps import get_registry, reset_deps
from backend.api.markets import router
from backend.instruments.registry import InstrumentRegistry

REQUIRED_PROVENANCE = {
    "source", "as_of", "delay_minutes",
    "quality_grade", "fallback_used", "missing_fields",
}


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path}/liquidity_history.db"
    monkeypatch.setenv("DATABASE_URL", url)
    from backend.db.session import reset_engine

    reset_engine()
    try:
        yield url
    finally:
        reset_engine()


def _client() -> TestClient:
    reset_deps()
    app = FastAPI()
    app.include_router(router)
    reg = InstrumentRegistry()
    app.dependency_overrides[get_registry] = lambda: reg
    deps_module._REGISTRY = reg  # type: ignore[attr-defined]
    return TestClient(app)


def _seed_two_symbols(url: str, days: int = 10):
    from backend.db.session import get_session_factory, init_db

    init_db(url)
    Session = get_session_factory()
    db = Session()
    try:
        from backend.db.models import Instrument as DBInst
        from backend.db.models import PriceBar

        now = datetime.now(timezone.utc)
        base = now - timedelta(days=days)
        for sym in ("AAPL", "MSFT"):
            inst = DBInst(
                instrument_id=uuid.uuid4(), exchange_mic="XNAS",
                exchange_symbol=sym, provider_symbol=sym,
                company_name=sym, currency="USD", country="US",
                sector="Tech", timezone="America/New_York",
                trading_calendar="XNAS", is_active=True,
            )
            db.add(inst)
            db.commit()
            for i in range(days):
                ts = base + timedelta(days=i)
                close = 100 + i * 2 if sym == "AAPL" else 200 - i
                db.add(PriceBar(
                    instrument_id=inst.instrument_id, ts=ts, timeframe="1d",
                    open=close - 1, high=close + 1, low=close - 2,
                    close=close, volume=1_000_000 + i * 10_000,
                    source="yfinance", as_of=now, quality_grade="C",
                ))
            db.commit()
    finally:
        try:
            db.close()
        except Exception:
            pass


def test_history_unknown_mic_422(isolated_db):
    c = _client()
    assert c.get("/api/markets/XXXX/liquidity/history").status_code == 422


def test_history_empty_db_returns_placeholder_not_500(isolated_db):
    from backend.db.session import init_db

    init_db(isolated_db)
    c = _client()
    r = c.get("/api/markets/XNAS/liquidity/history", params={"window": "1D"})
    assert r.status_code == 200
    body = r.json()
    assert body["mic"] == "XNAS"
    assert body["window"] == "1D"
    assert body["points"] == []
    assert set(body["provenance"]) >= REQUIRED_PROVENANCE


def test_history_seeded_db_returns_daily_points(isolated_db):
    _seed_two_symbols(isolated_db, days=10)
    c = _client()
    r = c.get("/api/markets/XNAS/liquidity/history", params={"window": "1M"})
    assert r.status_code == 200
    body = r.json()
    assert body["mic"] == "XNAS"
    assert body["currency"] == "USD"
    assert len(body["points"]) == 10
    first, second = body["points"][0], body["points"][1]
    assert first["turnover"] is not None and first["volume"] is not None
    # Breadth needs a previous day: first point has none, second has counts.
    assert first["advancers"] is None
    assert isinstance(second["advancers"], int)
    assert set(body["provenance"]) >= REQUIRED_PROVENANCE
    assert body["provenance"]["fallback_used"] is False


def test_history_bad_window_falls_back_to_1D(isolated_db):
    from backend.db.session import init_db

    init_db(isolated_db)
    c = _client()
    r = c.get("/api/markets/XNAS/liquidity/history", params={"window": "bogus"})
    assert r.status_code == 200
    assert r.json()["window"] == "1D"
