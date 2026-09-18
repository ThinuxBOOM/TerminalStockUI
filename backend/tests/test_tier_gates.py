"""V2 Phase 3 HARD tier gates (TestClient, isolated SQLite DB, no network).

Matrix (real JWT guards, live-row tiers):
  free -> deep_research 402
  free -> backtest 402
  silver -> backtest 402
  gold -> backtest 200
  spoofed X-Tier ignored (free JWT + X-Tier: platinum still 402)
  admin bypass (admin -> backtest 200, admin -> deep_research 202)
  unauthenticated -> 401

X-Tier header is NEVER trusted (guards never read it).
"""

from __future__ import annotations

import uuid

import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from backend.api.ai import router as ai_router
from backend.api.backtest import reset_backtest_history, router as backtest_router
from backend.auth.guards import create_access_token
from backend.db.models import Base
from backend.db.session import get_db, get_engine, init_db

TEST_SECRET = "test-tier-gates-secret-0123456789abcdef"


def _bars_payload(n: int = 250) -> dict:
    import numpy as np

    rng = np.random.RandomState(42)
    rets = 0.0005 + 0.01 * rng.randn(n)
    close = 100.0 * np.exp(np.cumsum(rets))
    dates = pd.bdate_range("2020-01-01", periods=n)
    vols = (1_000_000 + 200_000 * rng.randn(n)).clip(min=100_000)
    bars = []
    for d, c, v in zip(dates, close, vols):
        bars.append({
            "ts": d.isoformat(),
            "open": float(c * 0.999),
            "high": float(c * 1.001),
            "low": float(c * 0.998),
            "close": float(c),
            "volume": float(v),
        })
    return {
        "bars": bars,
        "provenance": {
            "source": "test-double",
            "as_of": "2026-09-12T00:00:00Z",
            "delay_minutes": 0,
            "quality_grade": "A",
            "fallback_used": False,
            "missing_fields": [],
        },
    }


class _FakeMarket:
    def get_bars(self, symbol: str, timeframe: str = "1d", limit: int = 30) -> dict:
        want = max(int(limit), 250)
        return _bars_payload(n=want)

    def get_quote(self, symbol: str, market=None, **kw) -> dict:  # pragma: no cover
        raise AssertionError("quote should not be hit in tier-gate tests")


def _seed_users(Session) -> dict[str, str]:
    """Create free/silver/gold/admin users; return {name: user_id}."""
    from backend.auth.guards import get_user_model

    User = get_user_model()
    db = Session()
    try:
        ids = {
            "free": str(uuid.uuid4()),
            "silver": str(uuid.uuid4()),
            "gold": str(uuid.uuid4()),
            "admin": str(uuid.uuid4()),
        }
        rows = [
            User(id=uuid.UUID(ids["free"]), email="free@test.local", password_hash="x", tier="free", is_admin=False),
            User(id=uuid.UUID(ids["silver"]), email="silver@test.local", password_hash="x", tier="silver", is_admin=False),
            User(id=uuid.UUID(ids["gold"]), email="gold@test.local", password_hash="x", tier="gold", is_admin=False),
            User(id=uuid.UUID(ids["admin"]), email="admin@test.local", password_hash="x", tier="platinum", is_admin=True),
        ]
        for r in rows:
            db.add(r)
        db.commit()
        return ids
    finally:
        db.close()


def _make_client(tmp_path, monkeypatch) -> tuple[TestClient, dict[str, str]]:
    monkeypatch.setenv("SECRET_KEY", TEST_SECRET)
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    # Ensure users table mapping exists before create_all.
    from backend.auth import guards as _g  # noqa: F401

    url = f"sqlite:///{tmp_path}/tiers.db"
    init_db(url)
    assert "users" in Base.metadata.tables
    Session = sessionmaker(bind=get_engine(url), autoflush=False, expire_on_commit=False)
    ids = _seed_users(Session)

    tokens = {
        name: create_access_token(uid, tier=("platinum" if name == "admin" else name),
                                  is_admin=(name == "admin"))
        for name, uid in ids.items()
    }

    reset_backtest_history()
    app = FastAPI()

    def _override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_db
    from backend.api import backtest as _bt

    fake = _FakeMarket()
    app.dependency_overrides[_bt.get_market_service] = lambda: fake  # type: ignore
    app.include_router(ai_router)
    app.include_router(backtest_router)
    client = TestClient(app, raise_server_exceptions=False)
    return client, tokens


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_free_to_deep_research_402(tmp_path, monkeypatch):
    client, tokens = _make_client(tmp_path, monkeypatch)
    r = client.post("/api/ai/deep_research_job", json={"symbol": "AAPL", "horizon": 21}, headers=_auth(tokens["free"]))
    assert r.status_code == 402, r.text
    assert "upgrade" in r.text.lower() or "silver" in r.text.lower()


def test_free_to_backtest_402(tmp_path, monkeypatch):
    client, tokens = _make_client(tmp_path, monkeypatch)
    r = client.post(
        "/api/backtest/run",
        json={"symbol": "AAPL", "horizons": [7], "train_size": 100, "test_size": 21, "gap": 7},
        headers=_auth(tokens["free"]),
    )
    assert r.status_code == 402, r.text


def test_silver_to_backtest_402(tmp_path, monkeypatch):
    client, tokens = _make_client(tmp_path, monkeypatch)
    r = client.post(
        "/api/backtest/run",
        json={"symbol": "AAPL", "horizons": [7], "train_size": 100, "test_size": 21, "gap": 7},
        headers=_auth(tokens["silver"]),
    )
    assert r.status_code == 402, r.text


def test_gold_to_backtest_200(tmp_path, monkeypatch):
    client, tokens = _make_client(tmp_path, monkeypatch)
    r = client.post(
        "/api/backtest/run",
        json={"symbol": "AAPL", "horizons": [7], "train_size": 100, "test_size": 21, "gap": 7},
        headers=_auth(tokens["gold"]),
    )
    assert r.status_code == 200, r.text
    assert r.json()["symbol"] == "AAPL"
    assert "7" in r.json()["results"]


def test_spoofed_x_tier_ignored(tmp_path, monkeypatch):
    client, tokens = _make_client(tmp_path, monkeypatch)
    headers = dict(_auth(tokens["free"]))
    headers["X-Tier"] = "platinum"
    r = client.post(
        "/api/backtest/run",
        json={"symbol": "AAPL", "horizons": [7], "train_size": 100, "test_size": 21, "gap": 7},
        headers=headers,
    )
    assert r.status_code == 402, r.text
    r2 = client.post("/api/ai/deep_research_job", json={"symbol": "AAPL", "horizon": 21}, headers=headers)
    assert r2.status_code == 402, r2.text


def test_admin_bypass(tmp_path, monkeypatch):
    client, tokens = _make_client(tmp_path, monkeypatch)
    r = client.post(
        "/api/backtest/run",
        json={"symbol": "AAPL", "horizons": [7], "train_size": 100, "test_size": 21, "gap": 7},
        headers=_auth(tokens["admin"]),
    )
    assert r.status_code == 200, r.text
    r2 = client.post("/api/ai/deep_research_job", json={"symbol": "AAPL", "horizon": 21}, headers=_auth(tokens["admin"]))
    assert r2.status_code == 202, r2.text


def test_unauthenticated_401(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch)
    r = client.post(
        "/api/backtest/run",
        json={"symbol": "AAPL", "horizons": [7], "train_size": 100, "test_size": 21, "gap": 7},
    )
    assert r.status_code == 401, r.text
