"""Retention cron endpoint: dry-run report + guarded apply (offline).

No network: seeds old/fresh rows in an isolated sqlite DB and drives
GET/POST /api/cron/retention through the full app. Covers: dry-run counts
without deleting, apply deletes only expired rows (fresh kept), garbage
overrides ignored, batch never 500s on DB failure, and CRON_SECRET auth
(401 without header, 200 with it when set; open in dev).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.api.cron import reset_cron
from backend.api.deps import reset_deps
from backend.api.main import create_app
from backend.db.models import Instrument, MarketSnapshot, PriceBar
from backend.db.session import reset_engine

PROVENANCE_KEYS = {
    "source", "as_of", "delay_minutes", "quality_grade",
    "fallback_used", "missing_fields",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path}/retention_cron.db"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.delenv("CRON_SECRET", raising=False)
    reset_engine()
    try:
        yield url
    finally:
        reset_engine()


def _client() -> TestClient:
    reset_deps()
    reset_cron()
    return TestClient(create_app())


def _teardown() -> None:
    reset_cron()
    reset_deps()
    reset_engine()


def _seed_old_and_fresh(isolated_db) -> None:
    from backend.db.session import get_session_factory, init_db

    init_db(isolated_db)
    Session = get_session_factory(isolated_db)
    db = Session()
    try:
        inst = Instrument(exchange_mic="XNAS", exchange_symbol="AAPL",
                          provider_symbol="AAPL", company_name="AAPL",
                          currency="USD")
        db.add(inst)
        db.flush()
        now = _utcnow()
        # price_bars: one expired (>5y), one fresh.
        db.add(PriceBar(instrument_id=inst.instrument_id,
                        ts=now - timedelta(days=6 * 365), timeframe="1d",
                        close=100.0, source="yfinance", as_of=now))
        db.add(PriceBar(instrument_id=inst.instrument_id,
                        ts=now - timedelta(days=2), timeframe="1d",
                        close=101.0, source="yfinance", as_of=now))
        # market_snapshots: old zstd row (raw tier, >30d), old gzip row
        # (compressed tier, >1y), fresh gzip row (kept).
        db.add(MarketSnapshot(
            symbol="AAPL", timeframe="1d", encoding="zstd", payload=b"z",
            n_bars=10, raw_bytes=100, compressed_bytes=50, source="yfinance",
            quality_grade="B", provenance={},
            ts=now - timedelta(days=60), created_at=now - timedelta(days=60)))
        db.add(MarketSnapshot(
            symbol="AAPL", timeframe="1d", encoding="gzip+json", payload=b"g",
            n_bars=10, raw_bytes=100, compressed_bytes=50, source="yfinance",
            quality_grade="B", provenance={},
            ts=now - timedelta(days=400), created_at=now - timedelta(days=400)))
        db.add(MarketSnapshot(
            symbol="AAPL", timeframe="1d", encoding="gzip+json", payload=b"g",
            n_bars=10, raw_bytes=100, compressed_bytes=50, source="alpaca",
            quality_grade="B", provenance={},
            ts=now - timedelta(days=2), created_at=now - timedelta(days=2)))
        db.commit()
    finally:
        db.close()


def _counts(url: str) -> tuple[int, int]:
    from backend.db.session import get_session_factory

    Session = get_session_factory(url)
    db = Session()
    try:
        bars = db.execute(select(func.count()).select_from(PriceBar)).scalar() or 0
        snaps = db.execute(select(func.count()).select_from(MarketSnapshot)).scalar() or 0
        return int(bars), int(snaps)
    finally:
        db.close()


def test_retention_get_is_dry_run(isolated_db):
    _seed_old_and_fresh(isolated_db)
    try:
        resp = _client().get("/api/cron/retention")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["deleted"] is False
        assert body["errors"] == {}
        assert body["counts"]["bars"] == 1
        assert body["counts"]["snapshots_raw"] == 1
        assert body["counts"]["snapshots_compressed"] == 1
        assert PROVENANCE_KEYS <= set(body["provenance"])
        # Nothing deleted by a dry run.
        assert _counts(isolated_db) == (2, 3)
    finally:
        _teardown()


def test_retention_post_apply_deletes_only_expired(isolated_db):
    _seed_old_and_fresh(isolated_db)
    try:
        client = _client()
        # apply=false behaves as a dry run.
        resp = client.post("/api/cron/retention", json={"apply": False})
        assert resp.status_code == 200, resp.text
        assert resp.json()["deleted"] is False
        assert _counts(isolated_db) == (2, 3)
        # apply=true deletes the expired rows, keeps fresh ones
        # (unknown override keys are ignored).
        resp = client.post("/api/cron/retention",
                           json={"apply": True,
                                 "retention_days": {"nope": 1}})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["deleted"] is True
        assert body["errors"] == {}
        bars, snaps = _counts(isolated_db)
        assert bars == 1  # expired bar gone, fresh bar kept
        assert snaps == 1  # only the fresh gzip snapshot kept
        from backend.db.session import get_session_factory

        Session = get_session_factory(isolated_db)
        db = Session()
        try:
            kept = db.execute(select(MarketSnapshot)).scalars().all()
            assert len(kept) == 1 and kept[0].source == "alpaca"
        finally:
            db.close()
    finally:
        _teardown()


def test_retention_auth(isolated_db, monkeypatch):
    _seed_old_and_fresh(isolated_db)
    monkeypatch.setenv("CRON_SECRET", "s3cr3t")
    try:
        client = _client()
        assert client.get("/api/cron/retention").status_code == 401
        assert client.post("/api/cron/retention",
                           json={"apply": False}).status_code == 401
        # Non-integer overrides are a strict 422 (fail-closed input contract).
        bad = client.post("/api/cron/retention",
                          json={"apply": True, "retention_days": {"bars": "bogus"}},
                          headers={"Authorization": "Bearer s3cr3t"})
        assert bad.status_code == 422
        headers = {"Authorization": "Bearer s3cr3t"}
        assert client.get("/api/cron/retention",
                          headers=headers).status_code == 200
        resp = client.post("/api/cron/retention", json={"apply": False},
                           headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["deleted"] is False
    finally:
        _teardown()
