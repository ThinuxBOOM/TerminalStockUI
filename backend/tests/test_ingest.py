"""Phase 1a ingest tests (offline only): idempotent upsert, cron auth,
DB-first get_bars + stub fallbacks. Never touches the network: the yfinance
fetch is always monkeypatched or bypassed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from backend.api.cron import reset_cron
from backend.api.deps import reset_deps
from backend.api.main import create_app
from backend.db.models import PriceBar
from backend.db.session import get_engine, reset_engine
from backend.instruments.registry import InstrumentRegistry
from backend.market_data import ingest as ingest_module
from backend.market_data.health import ProviderHealthTracker
from backend.market_data.ingest import DEFAULT_UNIVERSE, ingest_symbols
from backend.market_data.providers.base import ProviderError
from backend.market_data.providers.yfinance import YFinanceProvider
from backend.market_data.service import MarketDataService

PROVENANCE_KEYS = {
    "source", "as_of", "delay_minutes", "quality_grade",
    "fallback_used", "missing_fields",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _fake_bars(n: int = 120) -> list[dict]:
    """Deterministic fake daily bars (distinctive ~1000px closes)."""
    base = datetime(2024, 1, 2, tzinfo=timezone.utc)
    bars: list[dict] = []
    price = 1000.0
    for i in range(n):
        o = round(price, 2)
        c = round(price * 1.001, 2)
        bars.append({
            "ts": base + timedelta(days=i),
            "open": o,
            "high": round(max(o, c) * 1.002, 2),
            "low": round(min(o, c) * 0.998, 2),
            "close": c,
            "volume": 1_000_000 + i,
        })
        price = c
    return bars


def _fake_fetch(provider_symbol: str, period: str = "2y", interval: str = "1d"):
    assert period == "2y" and interval == "1d"  # single-fetch contract
    return _fake_bars(120)


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Point DATABASE_URL at a fresh sqlite file; drop the cached engine."""
    url = f"sqlite:///{tmp_path}/test.db"
    monkeypatch.setenv("DATABASE_URL", url)
    reset_engine()
    try:
        yield url
    finally:
        reset_engine()


@pytest.fixture
def patched_fetch(monkeypatch):
    monkeypatch.setattr(ingest_module, "fetch_daily_bars", _fake_fetch)


def _bar_count(url: str) -> int:
    Session = sessionmaker(
        bind=get_engine(url), autoflush=False, expire_on_commit=False
    )
    db = Session()
    try:
        return db.execute(select(func.count()).select_from(PriceBar)).scalar() or 0
    finally:
        db.close()


def _service() -> MarketDataService:
    tracker = ProviderHealthTracker()
    provider = YFinanceProvider(
        stub_mode=True, on_call=lambda p, ms, ok: tracker.record(p, ms, ok)
    )
    return MarketDataService(
        registry=InstrumentRegistry(), provider=provider,
        health=tracker, cache=None,
    )


def _client() -> TestClient:
    reset_deps()
    reset_cron()
    return TestClient(create_app())


def _teardown() -> None:
    reset_cron()
    reset_deps()
    reset_engine()


# --- upsert idempotency -------------------------------------------------------


def test_ingest_upsert_idempotent(isolated_db, patched_fetch):
    try:
        first, err1 = ingest_symbols(["AAPL"], registry=InstrumentRegistry())
        assert err1 == {}
        assert first.get("AAPL") == 120
        assert _bar_count(isolated_db) == 120
        second, err2 = ingest_symbols(["AAPL"], registry=InstrumentRegistry())
        assert err2 == {}
        assert second.get("AAPL") == 120
        assert _bar_count(isolated_db) == 120  # re-run merges, never duplicates
    finally:
        _teardown()


def test_ingest_unknown_symbol_reported_never_raises(isolated_db, patched_fetch):
    try:
        ingested, errors = ingest_symbols(
            ["AAPL", "ZZZ_NOPE_123"], registry=InstrumentRegistry()
        )
        assert ingested.get("AAPL") == 120
        assert "ZZZ_NOPE_123" in errors  # per-symbol entry, batch stays 200-able
        assert _bar_count(isolated_db) == 120
    finally:
        _teardown()


def test_ingest_fetch_failure_is_per_symbol_error(isolated_db):
    def _flaky(symbol: str, period: str = "2y", interval: str = "1d"):
        if symbol == "JPM":
            raise RuntimeError("yfinance down")
        return _fake_bars(120)

    try:
        ingested, errors = ingest_symbols(
            ["AAPL", "JPM"], registry=InstrumentRegistry(), fetch_fn=_flaky
        )
        assert ingested.get("AAPL") == 120
        assert "JPM" in errors
        assert _bar_count(isolated_db) == 120
    finally:
        _teardown()


# --- cron auth ----------------------------------------------------------------


def test_cron_auth_wrong_secret_401(isolated_db, patched_fetch, monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "s3cr3t")
    client = _client()
    try:
        assert client.get("/api/cron/ingest").status_code == 401  # missing header
        resp = client.get(
            "/api/cron/ingest",
            headers={"Authorization": "Bearer wrong"},
        )
        assert resp.status_code == 401
        resp = client.post("/api/cron/ingest", json={"symbols": ["AAPL"]})
        assert resp.status_code == 401
    finally:
        _teardown()


def test_cron_open_when_secret_unset_200(isolated_db, patched_fetch, monkeypatch):
    monkeypatch.delenv("CRON_SECRET", raising=False)
    client = _client()
    try:
        resp = client.get("/api/cron/ingest", params={"symbol": "AAPL"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        assert body["ingested"].get("AAPL") == 120
        assert body["errors"] == {}
        assert PROVENANCE_KEYS <= set(body["provenance"])
    finally:
        _teardown()


def test_cron_post_manual_symbols_mixed_known_unknown(
    isolated_db, patched_fetch, monkeypatch
):
    monkeypatch.delenv("CRON_SECRET", raising=False)
    client = _client()
    try:
        resp = client.post(
            "/api/cron/ingest", json={"symbols": ["MSFT", "ZZZ_NOPE_123"]}
        )
        assert resp.status_code == 200, resp.text  # never 500 the batch
        body = resp.json()
        assert body["ok"] is False  # batch ran, one symbol failed
        assert body["ingested"].get("MSFT") == 120
        assert "ZZZ_NOPE_123" in body["errors"]
        assert PROVENANCE_KEYS <= set(body["provenance"])
    finally:
        _teardown()


def test_cron_default_universe_and_env_override(
    isolated_db, patched_fetch, monkeypatch
):
    monkeypatch.delenv("CRON_SECRET", raising=False)
    client = _client()
    try:
        resp = client.get("/api/cron/ingest")
        assert resp.status_code == 200, resp.text
        assert set(resp.json()["ingested"]) == set(DEFAULT_UNIVERSE)

        monkeypatch.setenv("INGEST_SYMBOLS", "AAPL, MSFT")
        resp = client.get("/api/cron/ingest")
        assert resp.status_code == 200, resp.text
        assert set(resp.json()["ingested"]) == {"AAPL", "MSFT"}
    finally:
        _teardown()


# --- get_bars DB-first + fallbacks --------------------------------------------


def test_get_bars_db_first(isolated_db, monkeypatch):
    monkeypatch.setattr(
        ingest_module, "fetch_daily_bars",
        lambda symbol, period="2y", interval="1d": _fake_bars(150),
    )
    try:
        ingested, errors = ingest_symbols(["AAPL"], registry=InstrumentRegistry())
        assert errors == {} and ingested.get("AAPL") == 150

        svc = _service()
        out = svc.get_bars("AAPL", timeframe="1d", limit=150)
        assert out["provenance"]["fallback_used"] is False
        assert out["provenance"]["source"] == "yfinance"
        assert out["provenance"]["quality_grade"] in ("A", "B", "C", "D", "F")
        assert out["symbol"] == "AAPL"
        assert out["instrument_id"] == "XNAS-AAPL"
        assert len(out["bars"]) == 150
        expected_closes = [b["close"] for b in _fake_bars(150)]
        assert [b["close"] for b in out["bars"]] == expected_closes
        stamps = [b["ts"] for b in out["bars"]]
        assert stamps == sorted(stamps)  # ascending

        small = svc.get_bars("AAPL", timeframe="1d", limit=30)
        assert small["provenance"]["fallback_used"] is False
        assert len(small["bars"]) == 30
        assert [b["close"] for b in small["bars"]] == expected_closes[-30:]
    finally:
        _teardown()


def test_get_bars_empty_db_raises_provider_error(isolated_db, monkeypatch):
    # Fail-closed: empty DB + failed on-demand live fetch -> ProviderError
    # (no stub cover, no deterministic fallback bars).
    monkeypatch.setattr(
        ingest_module, "fetch_daily_bars",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    try:
        svc = _service()
        with pytest.raises(ProviderError):
            svc.get_bars("AAPL", timeframe="1d", limit=5)
    finally:
        _teardown()


def test_get_bars_unreachable_db_raises_provider_error(isolated_db, monkeypatch):
    # Fail-closed: unreachable DB + failed live fetch -> ProviderError
    # (never serve fabricated bars).
    import backend.db.session as session_module

    def _boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(session_module, "get_session_factory", _boom)
    # Offline: the on-demand live fetch also fails, so the request raises.
    monkeypatch.setattr(
        ingest_module, "fetch_daily_bars",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    try:
        svc = _service()
        with pytest.raises(ProviderError):
            svc.get_bars("AAPL", timeframe="1d", limit=7)
    finally:
        _teardown()

def test_get_or_create_survives_create_race(isolated_db):
    """Prod 23505/ShareLock regression: concurrent first-ingests of the same
    symbol must not raise -- the flush loser rolls back and returns the
    winner's row (exactly one row exists afterwards)."""
    from sqlalchemy.exc import IntegrityError

    from backend.db.models import Instrument as DBInstrument
    from backend.db.session import get_session_factory, init_db
    from backend.market_data.ingest import _get_or_create_db_instrument

    init_db()
    Session = get_session_factory()
    registry = InstrumentRegistry()
    inst, _, _ = registry.resolve("AAPL")
    assert inst is not None

    db = Session()
    try:
        real_flush = db.flush

        def _racy_flush():
            # Release our read snapshot so the "concurrent worker" can
            # commit, then simulate losing the UNIQUE race on flush.
            try:
                db.rollback()
            except Exception:
                pass
            winner = Session()
            try:
                _get_or_create_db_instrument(winner, inst)
                winner.commit()
            finally:
                winner.close()
            raise IntegrityError(
                "INSERT INTO instruments", {}, Exception("duplicate key"))

        db.flush = _racy_flush  # type: ignore[method-assign]
        row = _get_or_create_db_instrument(db, inst)
        assert row is not None
        assert row.exchange_mic == "XNAS"
        assert row.exchange_symbol == "AAPL"
        db.flush = real_flush  # type: ignore[method-assign]
        db.rollback()
    finally:
        db.close()

    checker = Session()
    try:
        n = checker.query(DBInstrument).filter(
            DBInstrument.exchange_mic == "XNAS",
            DBInstrument.exchange_symbol == "AAPL").count()
        assert n == 1
    finally:
        checker.close()
