"""Fail-closed persistence: live quotes write through to quote_snapshots,
outages raise (no snapshot cover, no placeholders), and first-view bars
are backfilled on demand. Offline only (no network)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.db.session import reset_engine
from backend.instruments.registry import InstrumentRegistry
from backend.market_data.health import ProviderHealthTracker
from backend.market_data.providers.base import ProviderError
from backend.market_data.service import MarketDataService


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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


class _FlipProvider:
    """Live quote until flipped, then a hard outage (raises)."""

    name = "yfinance"
    stub_mode = False

    def __init__(self) -> None:
        self.live = True
        self._on_call = None

    def get_quote(self, symbol: str) -> dict:
        upper = (symbol or "").strip().upper()
        if not upper:
            raise ProviderError(self.name, "empty symbol", retryable=False)
        if not self.live:
            raise ProviderError(self.name, "simulated outage")
        return {
            "symbol": upper,
            "price": 338.50,
            "open": 335.00,
            "high": 340.00,
            "low": 334.00,
            "prev_close": 332.60,
            "volume": 1_000_000,
            "currency": "USD",
            "change": 5.90,
            "change_pct": 1.77,
            "as_of": _utcnow(),
            "source": "yfinance",
            "missing_fields": [],
            "fallback_used": False,
        }


def _service(provider) -> MarketDataService:
    tracker = ProviderHealthTracker()
    return MarketDataService(
        registry=InstrumentRegistry(), provider=provider,
        health=tracker, cache=None,
    )


def _teardown() -> None:
    reset_engine()


def _snapshot_row(symbol: str):
    from backend.db.models import QuoteSnapshot
    from backend.db.session import get_session_factory, init_db

    init_db()
    db = get_session_factory()()
    try:
        return (
            db.query(QuoteSnapshot).filter(QuoteSnapshot.symbol == symbol).first()
        )
    finally:
        db.close()


def test_live_quote_persists_snapshot(isolated_db):
    try:
        svc = _service(_FlipProvider())
        quote = svc.get_quote("GOOGL")
        assert quote["provenance"]["fallback_used"] is False
        assert quote["price"] == 338.50
        row = _snapshot_row("GOOGL")
        assert row is not None
        assert float(row.price) == 338.50
        assert row.currency == "USD"
        # Stub data must never be persisted: outage BEFORE any live quote
        # leaves no row behind.
        assert _snapshot_row("ZZZ_UNKNOWN_123") is None
    finally:
        _teardown()


def test_outage_raises_despite_snapshot(isolated_db):
    # Fail-closed: a stored snapshot is never served as cover. Live first
    # (write-through persists), then outage raises ProviderError.
    try:
        provider = _FlipProvider()
        svc = _service(provider)
        live = svc.get_quote("GOOGL")
        assert live["provenance"]["fallback_used"] is False
        assert live["price"] == 338.50
        # Write-through persisted the live quote...
        assert _snapshot_row("GOOGL") is not None

        provider.live = False  # outage from here on
        # ...but the outage still raises instead of serving the snapshot.
        with pytest.raises(ProviderError):
            svc.get_quote("GOOGL")
    finally:
        _teardown()


def test_outage_without_history_raises_provider_error(isolated_db):
    """No snapshot ever stored -> fail-closed raise (never a stub)."""
    try:
        provider = _FlipProvider()
        provider.live = False
        svc = _service(provider)
        with pytest.raises(ProviderError):
            svc.get_quote("GOOGL")
        # Outage before any live quote leaves no row behind (stubs never
        # persist).
        assert _snapshot_row("GOOGL") is None
    finally:
        _teardown()


def test_bars_backfilled_on_demand_then_db_served(isolated_db, monkeypatch):
    """First view persists live bars; later views are DB-served real data."""
    from backend.market_data import ingest as ingest_module

    def _fake_fetch(provider_symbol: str, period="2y", interval="1d"):
        assert period == "2y" and interval == "1d"
        base = datetime(2024, 1, 2, tzinfo=timezone.utc)
        bars, price = [], 330.0
        for i in range(120):
            o = round(price, 2)
            c = round(price * 1.001, 2)
            bars.append({
                "ts": base.replace(),  # placeholder replaced below
                "open": o,
                "high": round(max(o, c) * 1.002, 2),
                "low": round(min(o, c) * 0.998, 2),
                "close": c,
                "volume": 1_000_000 + i,
            })
            price = c
        from datetime import timedelta

        for i, bar in enumerate(bars):
            bar["ts"] = base + timedelta(days=i)
        return bars

    try:
        monkeypatch.setattr(ingest_module, "fetch_daily_bars", _fake_fetch)
        svc = _service(_FlipProvider())
        first = svc.get_bars("GOOGL", timeframe="1d", limit=120)
        assert first["provenance"]["fallback_used"] is False
        assert len(first["bars"]) == 120

        def _boom(symbol: str, period="2y", interval="1d"):
            raise RuntimeError("network down")

        monkeypatch.setattr(ingest_module, "fetch_daily_bars", _boom)
        # NOTE: same service would hit the service-level flow again; build a
        # fresh one to prove the data survived in the DB, not in memory.
        svc2 = _service(_FlipProvider())
        second = svc2.get_bars("GOOGL", timeframe="1d", limit=120)
        assert second["provenance"]["fallback_used"] is False
        assert [b["close"] for b in second["bars"]] == [
            b["close"] for b in first["bars"]
        ]
    finally:
        _teardown()


def test_bars_miss_is_negatively_cached(isolated_db, monkeypatch):
    """Unfetchable symbols fail fast on repeat views (no timeout loop)."""
    from backend.cache import InMemoryCache
    from backend.market_data import ingest as ingest_module

    calls: list[str] = []

    def _boom(symbol: str, period="2y", interval="1d"):
        calls.append(symbol)
        raise RuntimeError("network down")

    try:
        monkeypatch.setattr(ingest_module, "fetch_daily_bars", _boom)
        tracker = ProviderHealthTracker()
        svc = MarketDataService(
            registry=InstrumentRegistry(),
            provider=_FlipProvider(),
            health=tracker,
            cache=InMemoryCache(),
        )
        # AAPL resolves in-registry; fetch fails -> fail-closed raise.
        with pytest.raises(ProviderError):
            svc.get_bars("AAPL", timeframe="1d", limit=5)
        assert len(calls) == 1
        with pytest.raises(ProviderError):
            svc.get_bars("AAPL", timeframe="1d", limit=5)
        assert len(calls) == 1  # second view skipped the doomed fetch
    finally:
        _teardown()


def test_write_through_persists_live_quote_only(isolated_db):
    """_persist_quote_snapshot stores live-shaped quotes, never outages."""
    try:
        svc = _service(_FlipProvider())
        quote = svc.get_quote("GOOGL")
        assert quote["provenance"]["fallback_used"] is False
        row = _snapshot_row("GOOGL")
        assert row is not None
        assert float(row.price) == 338.50
        assert row.currency == "USD"
        assert row.source == "yfinance"
    finally:
        _teardown()


def test_snapshot_instrument_id_never_registry_string(isolated_db):
    """Prod 22P02 regression: quote_snapshots.instrument_id is a UUID FK.

    The registry id ("XNAS-AAPL"-style) must never reach the UUID column —
    every live quote used to emit ``invalid input syntax for type uuid`` on
    Postgres (SQLite silently stored the string, hiding the bug).
    """
    try:
        svc = _service(_FlipProvider())
        quote = svc.get_quote("AAPL")
        assert quote["provenance"]["fallback_used"] is False
        row = _snapshot_row("AAPL")
        assert row is not None
        assert row.instrument_id is None
    finally:
        _teardown()
