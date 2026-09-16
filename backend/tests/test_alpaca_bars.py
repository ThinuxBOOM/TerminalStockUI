"""Alpaca daily bars: parse, chain, and same-feed backfill (offline).

No network: ``httpx.get`` is monkeypatched with canned
``/v2/stocks/.../bars`` payloads; Alpaca keys come from the environment
(monkeypatched). Covers: bar parsing + UTC normalization, pagination,
non-US fast-fail (no HTTP), missing-keys fast-fail, chain fallback order
(Alpaca first, yfinance cover), ``bar_chain()`` defaults with/without
keys, and the service on-demand backfill selecting Alpaca for eligible
symbols while SSE stays yfinance-only with the winning source persisted.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from backend.db.models import PriceBar
from backend.db.session import get_session_factory
from backend.instruments.registry import InstrumentRegistry
from backend.market_data import ingest as ingest_module
from backend.market_data.health import ProviderHealthTracker
from backend.market_data.ingest import (
    bar_chain,
    fetch_alpaca_daily_bars,
    fetch_daily_bars_with_fallback,
)
from backend.market_data.providers.yfinance import YFinanceProvider
from backend.market_data.service import MarketDataService


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path}/alpaca_bars.db"
    monkeypatch.setenv("DATABASE_URL", url)
    # Chain/key env pinned: tests control Alpaca availability explicitly.
    monkeypatch.delenv("INGEST_BAR_CHAIN", raising=False)
    monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET_KEY", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    from backend.db.session import reset_engine

    reset_engine()
    try:
        yield url
    finally:
        reset_engine()


@pytest.fixture
def alpaca_keys(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY_ID", "test-key-id")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "test-secret")


def _page(rows, token=None):
    payload = {"bars": rows, "symbol": "AAPL"}
    if token:
        payload["next_page_token"] = token
    return payload


def _row(t, o, h, low, c, v):
    return {"t": t, "o": o, "h": h, "l": low, "c": c, "v": v, "n": 10, "vw": c}


class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


def _httpx_fake(monkeypatch, pages, *, statuses=None, record=None):
    import httpx

    calls: list[dict] = []

    def _get(url, params=None, headers=None, timeout=None):
        calls.append({"url": url, "params": dict(params or {}),
                      "headers": dict(headers or {})})
        idx = min(len(calls) - 1, len(pages) - 1)
        status = (statuses or [200])[min(len(calls) - 1, len(statuses or [200]) - 1)]
        assert "APCA-API-KEY-ID" in (headers or {}), "key id header missing"
        assert "APCA-API-SECRET-KEY" in (headers or {}), "secret header missing"
        return _FakeResp(pages[idx], status=status)

    monkeypatch.setattr(httpx, "get", _get)
    if record is not None:
        record.extend(calls)  # caller reads `calls` directly instead
    return calls


# --- parsing ---------------------------------------------------------------

def test_alpaca_bars_parse_and_utc(isolated_db, alpaca_keys, monkeypatch):
    rows = [
        _row("2024-01-02T05:00:00Z", 100.0, 101.0, 99.5, 100.5, 1000000),
        _row("2024-01-03T05:00:00Z", 100.5, 102.0, 100.0, 101.5, 1100000),
    ]
    calls = _httpx_fake(monkeypatch, [_page(rows)])
    bars = fetch_alpaca_daily_bars("AAPL")
    assert len(bars) == 2
    assert bars[0]["close"] == pytest.approx(100.5)
    assert bars[0]["volume"] == 1000000
    assert bars[0]["ts"].tzinfo is not None
    assert (bars[0]["ts"].date().isoformat(), bars[1]["ts"].date().isoformat()) == (
        "2024-01-02", "2024-01-03")
    # adjustment=all (split/dividend lineage matches yfinance auto_adjust).
    assert calls[0]["params"]["adjustment"] == "all"
    assert calls[0]["params"]["timeframe"] == "1Day"
    assert calls[0]["params"]["sort"] == "asc"
    assert calls[0]["url"].endswith("/v2/stocks/AAPL/bars")


def test_alpaca_bars_pagination(isolated_db, alpaca_keys, monkeypatch):
    p1 = [_row("2024-01-02T05:00:00Z", 1.0, 1.0, 1.0, 1.0, 10)]
    p2 = [_row("2024-01-03T05:00:00Z", 2.0, 2.0, 2.0, 2.0, 20)]
    _httpx_fake(monkeypatch, [_page(p1, token="abc"), _page(p2)])
    bars = fetch_alpaca_daily_bars("AAPL")
    assert [b["close"] for b in bars] == [1.0, 2.0]


def test_alpaca_bars_skips_null_close_rows(isolated_db, alpaca_keys, monkeypatch):
    rows = [_row("2024-01-02T05:00:00Z", 1.0, 1.0, 1.0, None, 10),
            _row("2024-01-03T05:00:00Z", 2.0, 2.0, 2.0, 2.0, 20)]
    _httpx_fake(monkeypatch, [_page(rows)])
    bars = fetch_alpaca_daily_bars("AAPL")
    assert len(bars) == 1 and bars[0]["close"] == 2.0


def test_alpaca_bars_non_us_fails_before_network(isolated_db, alpaca_keys, monkeypatch):
    import httpx

    def _boom(*a, **k):
        raise AssertionError("no HTTP for non-US symbols")

    monkeypatch.setattr(httpx, "get", _boom)
    with pytest.raises(Exception):
        fetch_alpaca_daily_bars("600519.SS")
    with pytest.raises(Exception):
        fetch_alpaca_daily_bars("MC.PA")


def test_alpaca_bars_missing_keys_fails_before_network(isolated_db, monkeypatch):
    import httpx

    def _boom(*a, **k):
        raise AssertionError("no HTTP without keys")

    monkeypatch.setattr(httpx, "get", _boom)
    with pytest.raises(Exception):
        fetch_alpaca_daily_bars("AAPL")


def test_alpaca_bars_http_errors_raise(isolated_db, alpaca_keys, monkeypatch):
    _httpx_fake(monkeypatch, [_page([])], statuses=[401])
    with pytest.raises(Exception):
        fetch_alpaca_daily_bars("AAPL")


# --- chain ------------------------------------------------------------------

def test_chain_alpaca_first_yfinance_cover(isolated_db, alpaca_keys, monkeypatch):
    canned = [{"ts": datetime(2024, 1, 2, tzinfo=timezone.utc), "open": 1.0,
               "high": 1.0, "low": 1.0, "close": 1.0, "volume": 10}]
    monkeypatch.setattr(ingest_module, "fetch_alpaca_daily_bars",
                        lambda symbol, **k: list(canned))
    seen: list[str] = []

    def _yf(symbol, period="2y", interval="1d"):
        seen.append(symbol)
        raise RuntimeError("must not reach yfinance")

    monkeypatch.setattr(ingest_module, "fetch_daily_bars", _yf)
    bars, source = fetch_daily_bars_with_fallback("AAPL", chain=["alpaca", "yfinance"])
    assert source == "alpaca" and bars == canned and seen == []


def test_chain_falls_through_to_yfinance(isolated_db, monkeypatch):
    def _alpaca_boom(symbol, **k):
        raise RuntimeError("alpaca API keys missing")

    canned = [{"ts": datetime(2024, 1, 2, tzinfo=timezone.utc), "open": 2.0,
               "high": 2.0, "low": 2.0, "close": 2.0, "volume": 20}]
    monkeypatch.setattr(ingest_module, "fetch_alpaca_daily_bars", _alpaca_boom)
    monkeypatch.setattr(ingest_module, "fetch_daily_bars",
                        lambda symbol, period="2y", interval="1d": list(canned))
    bars, source = fetch_daily_bars_with_fallback("AAPL", chain=["alpaca", "yfinance"])
    assert source == "yfinance" and bars == canned


def test_bar_chain_defaults_and_env(isolated_db, monkeypatch, alpaca_keys):
    assert bar_chain() == ["alpaca", "yfinance", "stooq"]
    monkeypatch.setenv("INGEST_BAR_CHAIN", "stooq,yfinance")
    assert bar_chain() == ["stooq", "yfinance"]
    monkeypatch.setenv("INGEST_BAR_CHAIN", "alpaca,typo!!,yfinance")
    assert bar_chain() == ["alpaca", "yfinance"]


def test_bar_chain_without_keys(isolated_db, monkeypatch):
    assert bar_chain() == ["yfinance", "stooq"]


# --- service on-demand backfill ---------------------------------------------

def _service() -> MarketDataService:
    tracker = ProviderHealthTracker()
    provider = YFinanceProvider(
        stub_mode=True, on_call=lambda p, ms, ok: tracker.record(p, ms, ok)
    )
    return MarketDataService(
        registry=InstrumentRegistry(), provider=provider,
        health=tracker, cache=None,
    )


def _120_bars():
    from datetime import timedelta

    base = datetime(2024, 1, 2, tzinfo=timezone.utc)
    return [{
        "ts": base + timedelta(days=i), "open": 100.0, "high": 101.0,
        "low": 99.0, "close": 100.5, "volume": 1000000,
    } for i in range(120)]


def test_ondemand_backfill_prefers_alpaca_for_us(isolated_db, monkeypatch, alpaca_keys):
    from backend.market_data import ingest as ing

    seen: dict[str, object] = {}

    def _chain(symbol, chain=None, **k):
        seen["chain"] = list(chain or [])
        return list(_120_bars()), "alpaca"

    monkeypatch.setattr(ing, "fetch_daily_bars_with_fallback", _chain)
    svc = _service()
    assert svc._fetch_and_store_bars("AAPL", "1d") is True
    assert seen["chain"] == ["alpaca", "yfinance"]
    Session = get_session_factory()
    db = Session()
    try:
        rows = db.execute(select(PriceBar)).scalars().all()
        assert len(rows) == 120
        assert {r.source for r in rows} == {"alpaca"}
    finally:
        db.close()


def test_ondemand_backfill_sse_stays_yfinance(isolated_db, monkeypatch, alpaca_keys):
    from backend.market_data import ingest as ing

    seen: dict[str, object] = {}

    def _chain(symbol, chain=None, **k):
        seen["chain"] = list(chain or [])
        assert symbol == "600519.SS"
        return list(_120_bars()), "yfinance"

    monkeypatch.setattr(ing, "fetch_daily_bars_with_fallback", _chain)
    svc = _service()
    assert svc._fetch_and_store_bars("600519.SS", "1d") is True
    assert seen["chain"] == ["yfinance"]


def test_ondemand_backfill_yfinance_when_unconfigured(isolated_db, monkeypatch):
    from backend.market_data import ingest as ing

    seen: dict[str, object] = {}

    def _chain(symbol, chain=None, **k):
        seen["chain"] = list(chain or [])
        return list(_120_bars()), "yfinance"

    monkeypatch.setattr(ing, "fetch_daily_bars_with_fallback", _chain)
    svc = _service()
    assert svc._fetch_and_store_bars("AAPL", "1d") is True
    assert seen["chain"] == ["yfinance"]
