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


def _seed_fresh_yahoo_bars(url: str, symbol: str = "AAPL"):
    """Seed 120 fresh yfinance rows ending the last completed XNAS session."""
    from datetime import timedelta
    from backend.db.models import Instrument as DBInstrument
    from backend.db.models import PriceBar as _PriceBar
    from backend.db.session import get_session_factory, init_db
    from backend.instruments.calendars import last_completed_trading_day

    end_day = last_completed_trading_day("XNAS")
    init_db(url)
    Session = get_session_factory(url)
    db = Session()
    try:
        inst = DBInstrument(
            exchange_mic="XNAS", exchange_symbol=symbol,
            provider_symbol=symbol, company_name=symbol, currency="USD",
        )
        db.add(inst)
        db.flush()
        price = 300.0
        for i in range(120):
            day = end_day - timedelta(days=(119 - i))
            ts = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
            o = round(price, 2)
            c = round(price * 1.001, 2)
            db.add(_PriceBar(
                instrument_id=inst.instrument_id, ts=ts, timeframe="1d",
                open=o, high=round(max(o, c) * 1.002, 2),
                low=round(min(o, c) * 0.998, 2), close=c,
                volume=1_000_000 + i, source="yfinance",
                as_of=datetime.now(timezone.utc), quality_grade="B",
            ))
            price = c
        db.commit()
    finally:
        db.close()
    return end_day


def _alpaca_bars_on_grid(end_day, close_base: float = 500.0):
    """120 Alpaca bars on the SAME calendar grid (merge overwrites by PK)."""
    from datetime import timedelta

    bars = []
    price = close_base
    for i in range(120):
        day = end_day - timedelta(days=(119 - i))
        o = round(price, 2)
        c = round(price * 1.001, 2)
        bars.append({
            "ts": datetime(day.year, day.month, day.day, tzinfo=timezone.utc),
            "open": o, "high": round(max(o, c) * 1.002, 2),
            "low": round(min(o, c) * 0.998, 2), "close": c,
            "volume": 2_000_000 + i,
        })
        price = c
    return bars


def test_ondemand_backfill_prefers_alpaca_for_us(isolated_db, monkeypatch, alpaca_keys):
    from backend.market_data import ingest as ing

    calls: list[str] = []

    def _alpaca(symbol, **k):
        calls.append(f"alpaca:{symbol}")
        return list(_120_bars())

    def _yf(symbol, period="2y", interval="1d"):
        calls.append(f"yfinance:{symbol}")
        return list(_120_bars())

    monkeypatch.setattr(ing, "fetch_alpaca_daily_bars", _alpaca)
    monkeypatch.setattr(ing, "fetch_daily_bars", _yf)
    svc = _service()
    assert svc._fetch_and_store_bars("AAPL", "1d") is True
    assert calls == ["alpaca:AAPL"]  # yfinance never touched
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

    def _alpaca(symbol, **k):
        raise AssertionError("SSE must never touch Alpaca")

    def _yf(symbol, period="2y", interval="1d"):
        assert symbol == "600519.SS"
        return list(_120_bars())

    monkeypatch.setattr(ing, "fetch_alpaca_daily_bars", _alpaca)
    monkeypatch.setattr(ing, "fetch_daily_bars", _yf)
    svc = _service()
    assert svc._fetch_and_store_bars("600519.SS", "1d") is True


def test_ondemand_backfill_yfinance_when_unconfigured(isolated_db, monkeypatch):
    from backend.market_data import ingest as ing

    def _alpaca(symbol, **k):
        raise AssertionError("unconfigured keys must not attempt Alpaca")

    def _yf(symbol, period="2y", interval="1d"):
        return list(_120_bars())

    monkeypatch.setattr(ing, "fetch_alpaca_daily_bars", _alpaca)
    monkeypatch.setattr(ing, "fetch_daily_bars", _yf)
    svc = _service()
    assert svc._fetch_and_store_bars("AAPL", "1d") is True


def test_get_bars_refreshes_yahoo_rows_from_alpaca(isolated_db, monkeypatch, alpaca_keys):
    """Fresh yfinance DB rows are refreshed (not served) for US symbols."""
    from backend.market_data import ingest as ing

    end_day = _seed_fresh_yahoo_bars(isolated_db)
    monkeypatch.setattr(ing, "fetch_alpaca_daily_bars",
                        lambda symbol, **k: _alpaca_bars_on_grid(end_day))
    svc = _service()
    out = svc.get_bars("AAPL", timeframe="1d", limit=120)
    assert len(out["bars"]) == 120
    assert out["provenance"]["source"] == "alpaca"
    assert out["provenance"]["fallback_used"] is False
    # Alpaca values overwrote the seeded ~300s with ~500s on the same grid.
    assert out["bars"][0]["close"] == pytest.approx(
        _alpaca_bars_on_grid(end_day)[0]["close"])
    assert out["bars"][0]["close"] > 400.0


def test_get_bars_serves_yahoo_when_alpaca_down_no_502(isolated_db, monkeypatch, alpaca_keys):
    """Alpaca outage + fresh yfinance rows: serve honestly, cool down."""
    from backend.market_data import ingest as ing

    _seed_fresh_yahoo_bars(isolated_db)
    attempts: list[str] = []

    def _boom(symbol, **k):
        attempts.append(symbol)
        raise RuntimeError("alpaca down")

    monkeypatch.setattr(ing, "fetch_alpaca_daily_bars", _boom)
    svc = _service()
    out = svc.get_bars("AAPL", timeframe="1d", limit=120)
    assert len(out["bars"]) == 120
    assert out["provenance"]["source"] == "yfinance"  # honest badge
    assert attempts == ["AAPL"]
    # Cooldown: second view serves DB without another slow Alpaca attempt.
    out2 = svc.get_bars("AAPL", timeframe="1d", limit=120)
    assert len(out2["bars"]) == 120
    assert attempts == ["AAPL"]


def test_get_bars_no_alpaca_attempt_unconfigured_or_non_1d(
    isolated_db, monkeypatch, alpaca_keys
):
    from backend.market_data import ingest as ing

    def _boom(symbol, **k):
        raise AssertionError("Alpaca must not be attempted")

    monkeypatch.setattr(ing, "fetch_alpaca_daily_bars", _boom)
    # Non-1d timeframe: refresh impossible -> serve/fail as today.
    svc = _service()
    _seed_fresh_yahoo_bars(isolated_db)
    with pytest.raises(Exception):
        svc.get_bars("AAPL", timeframe="1wk", limit=5)


def test_get_bars_no_alpaca_attempt_without_keys(isolated_db, monkeypatch):
    from backend.market_data import ingest as ing

    def _boom(symbol, **k):
        raise AssertionError("unconfigured keys must not attempt Alpaca")

    monkeypatch.setattr(ing, "fetch_alpaca_daily_bars", _boom)
    _seed_fresh_yahoo_bars(isolated_db)
    svc = _service()
    out = svc.get_bars("AAPL", timeframe="1d", limit=120)
    assert len(out["bars"]) == 120
    assert out["provenance"]["source"] == "yfinance"


def test_ondemand_backfill_alpaca_miss_falls_back_and_cools_down(
    isolated_db, monkeypatch, alpaca_keys
):
    from backend.market_data import ingest as ing

    attempts: list[str] = []

    def _alpaca_boom(symbol, **k):
        attempts.append(symbol)
        raise RuntimeError("alpaca down")

    def _yf(symbol, period="2y", interval="1d"):
        return list(_120_bars())

    monkeypatch.setattr(ing, "fetch_alpaca_daily_bars", _alpaca_boom)
    monkeypatch.setattr(ing, "fetch_daily_bars", _yf)
    svc = _service()
    assert svc._fetch_and_store_bars("AAPL", "1d") is True
    assert attempts == ["AAPL"]
    # Cooldown: the next backfill skips Alpaca (one slow view per outage).
    assert svc._fetch_and_store_bars("MSFT", "1d") is True
    assert attempts == ["AAPL"]
    Session = get_session_factory()
    db = Session()
    try:
        rows = db.execute(select(PriceBar)).scalars().all()
        assert rows and {r.source for r in rows} == {"yfinance"}
    finally:
        db.close()
