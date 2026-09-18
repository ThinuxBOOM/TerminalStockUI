"""Cron serverless-budget regression tests (Actions curl exit-28 fix).

Root cause: both tick paths collected vendor work under a time budget but
then blocked the HTTP response past it —
``ThreadPoolExecutor.__exit__`` (shutdown(wait=True)) waited for straggler
fetches, and the ingest persist phase ran unbounded after the fetch pool.
Result: curl --max-time 55/58 expired with HTTP 000 (zero bytes).

These tests pin the fixed contract with hermetic slow stubs (no network):
  * slow vendor fetch  -> returns inside the budget, remainder marked
    "fetch budget exceeded (retry next tick)" (never holds the response).
  * expired tick       -> fetched-but-unpersisted symbols are marked
    "persist deferred (retry next tick)" instead of committing past deadline.
  * slow snapshot job -> returns inside the snapshot budget with per-symbol
    "skipped: snapshot time budget exceeded" + truncated=True.

Wall-clock asserts are generous (slow CI) but far below the real curl
windows, so a regression to blocking-shutdown fails loudly.
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.api.cron as cron
import backend.db.session as sess
import backend.workers.jobs as jobs
from backend.db.models import Base
from backend.db.session import reset_engine
from backend.instruments.registry import InstrumentRegistry
from backend.market_data.ingest import ingest_symbols

SYMBOLS = ["AAPL", "MSFT", "NVDA", "TSLA", "GOOGL", "AMZN"]


def _fake_bars(n: int = 5) -> list[dict]:
    from datetime import datetime, timedelta, timezone

    base = datetime(2024, 1, 2, tzinfo=timezone.utc)
    return [
        {
            "ts": base + timedelta(days=i),
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 1_000_000,
        }
        for i in range(n)
    ]


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Point DATABASE_URL at a fresh sqlite file; drop the cached engine."""
    url = f"sqlite:///{tmp_path}/timeouts.db"
    monkeypatch.setenv("DATABASE_URL", url)
    reset_engine()
    try:
        yield url
    finally:
        reset_engine()


def _slow_fetch(provider_symbol: str):
    time.sleep(12)
    return _fake_bars(), "yfinance"


def test_ingest_slow_fetch_returns_inside_budget(isolated_db):
    """Hanging vendor calls must not hold the tick response past budget."""
    started = time.monotonic()
    ingested, errors = ingest_symbols(
        SYMBOLS,
        registry=InstrumentRegistry(),
        fetch_fn=_slow_fetch,
        budget_s=4.0,
    )
    elapsed = time.monotonic() - started
    assert elapsed < 30, f"tick blocked on stragglers: {elapsed:.1f}s"
    assert ingested == {}
    assert set(errors) == set(SYMBOLS)
    assert all(v == "fetch budget exceeded (retry next tick)" for v in errors.values())


def test_ingest_expired_tick_defers_persist(isolated_db):
    """budget_s=0: everything fetched is deferred, nothing committed past deadline."""
    def _fast_fetch(provider_symbol: str):
        return _fake_bars(), "yfinance"

    started = time.monotonic()
    ingested, errors = ingest_symbols(
        SYMBOLS,
        registry=InstrumentRegistry(),
        fetch_fn=_fast_fetch,
        budget_s=0,
    )
    elapsed = time.monotonic() - started
    assert elapsed < 30, f"persist phase ran unbounded: {elapsed:.1f}s"
    assert ingested == {}
    assert set(errors) == set(SYMBOLS)
    assert all(v == "persist deferred (retry next tick)" for v in errors.values())


def test_snapshot_slow_capture_returns_inside_budget(monkeypatch):
    """Hanging snapshot jobs return partial 200 inside the budget."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(sess, "init_db", lambda *a, **k: None)
    monkeypatch.setattr(sess, "get_session_factory", lambda *a, **k: factory)
    monkeypatch.setattr(cron, "_SNAPSHOT_BUDGET_S", 3.0)

    def _slow_capture(symbol: str, timeframe: str = "1d", db=None):
        time.sleep(12)
        return {"snapshot_id": "never", "persisted": False, "ok": True,
                "encoding": "none", "n_bars": 0, "size_reduction_pct": 0.0}

    monkeypatch.setattr(jobs, "capture_snapshot", _slow_capture)

    symbols = ["S1", "S2", "S3", "S4", "S5"]  # >3 forces the parallel path
    started = time.monotonic()
    out = cron._run_snapshot(symbols, "1d")
    elapsed = time.monotonic() - started
    assert elapsed < 30, f"snapshot blocked on stragglers: {elapsed:.1f}s"
    assert out["snapshots"] == {}
    assert set(out["errors"]) == {"S1", "S2", "S3", "S4", "S5"}
    # Either the per-future join timed out ("skipped: TimeoutError ...") or
    # the deadline passed first ("skipped: ... time budget exceeded") —
    # both are the budget path, never a held-open response.
    assert all(v.startswith("skipped: ") for v in out["errors"].values())
    assert out.get("truncated") is True
