"""Single-call chart: live quote + bars stitched with it (offline).

Covers the quote-vs-bars skew fix: ``get_chart`` fetches the bars series
and the live quote in one backend handling and overlays the quote onto the
terminal 1d bar, so the header price and the chart's last print are the
same number from the same call. No network (service methods stubbed at
the instance level); forecasting keeps reading unstitched ``get_bars``.
"""

from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_market_service, reset_deps
from backend.api.main import create_app
from backend.market_data.providers.base import ProviderError
from backend.market_data.service import MarketDataService

PROVENANCE_KEYS = {
    "source", "as_of", "delay_minutes", "quality_grade",
    "fallback_used", "missing_fields",
}


def _bars(last_close=331.50, last_ts="2026-09-16T04:00:00+00:00"):
    return {
        "symbol": "AAPL",
        "instrument_id": "XNAS-AAPL",
        "timeframe": "1d",
        "bars": [
            {"ts": "2026-09-15T04:00:00+00:00", "open": 330.0, "high": 331.0,
             "low": 329.0, "close": 330.5, "volume": 100, "missing_fields": []},
            {"ts": last_ts, "open": 331.0, "high": 332.0,
             "low": 330.0, "close": last_close, "volume": 110,
             "missing_fields": []},
        ],
        "provenance": {"source": "alpaca", "as_of": last_ts,
                       "delay_minutes": 0, "quality_grade": "B",
                       "fallback_used": False, "missing_fields": []},
    }


def _quote(price=333.28, as_of="2026-09-16T19:00:00+00:00", volume=500):
    return {
        "symbol": "AAPL", "price": price, "open": 331.0, "high": 333.0,
        "low": 330.5, "prev_close": 330.5, "volume": volume,
        "currency": "USD", "change": 2.78, "change_pct": 0.84,
        "market_state": "delayed",
        "provenance": {"source": "alpaca", "as_of": as_of,
                       "delay_minutes": 0, "quality_grade": "B",
                       "fallback_used": False, "missing_fields": []},
    }


def _service(bars=None, quote=None, bars_error=None, quote_error=None):
    svc = MarketDataService()

    def _get_bars(symbol, timeframe="1d", limit=30):
        if bars_error is not None:
            raise bars_error
        return copy.deepcopy(bars if bars is not None else _bars())

    def _get_quote(symbol, market=None, **kw):
        if quote_error is not None:
            raise quote_error
        return copy.deepcopy(quote if quote is not None else _quote())

    svc.get_bars = _get_bars  # type: ignore[method-assign]
    svc.get_quote = _get_quote  # type: ignore[method-assign]
    return svc


def _client(svc) -> TestClient:
    reset_deps()
    app = create_app()
    app.dependency_overrides[get_market_service] = lambda: svc
    return TestClient(app)


def _teardown() -> None:
    reset_deps()


# --- stitch matrix ----------------------------------------------------------

def test_chart_stitches_same_session_quote():
    svc = _service()
    out = svc.get_chart("AAPL", "1d", 30)
    assert out["stitched"] is True
    assert out["stitched_reason"] is None
    # Header price and chart last print: same number, same call.
    assert out["quote"]["price"] == pytest.approx(333.28)
    assert out["bars"][-1]["close"] == pytest.approx(out["quote"]["price"])
    assert out["bars"][-1]["high"] == pytest.approx(333.28)  # extended
    assert out["bars"][-1]["low"] == pytest.approx(330.0)  # untouched
    assert out["bars"][-1]["volume"] == 500  # quote session volume wins
    assert out["bars"][-2]["close"] == pytest.approx(330.5)  # history intact
    assert len(out["bars"]) == 2  # never appends
    assert PROVENANCE_KEYS <= set(out["provenance"])


def test_chart_quote_inside_range_only_moves_close():
    svc = _service(quote=_quote(price=331.0))
    out = svc.get_chart("AAPL", "1d", 30)
    assert out["stitched"] is True
    assert out["bars"][-1]["close"] == pytest.approx(331.0)
    assert out["bars"][-1]["high"] == pytest.approx(332.0)
    assert out["bars"][-1]["low"] == pytest.approx(330.0)


def test_chart_quote_newer_session_appends_forming_bar():
    svc = _service(quote=_quote(as_of="2026-09-17T13:30:00+00:00"))
    out = svc.get_chart("AAPL", "1d", 30)
    assert out["stitched"] is True
    assert out["stitched_reason"] is None
    assert out["forming"] is True
    assert len(out["bars"]) == 3
    forming = out["bars"][-1]
    # Every forming field comes from the quote itself — nothing fabricated.
    assert forming["open"] == pytest.approx(331.0)
    assert forming["close"] == pytest.approx(out["quote"]["price"])
    assert forming["high"] == pytest.approx(333.28)
    assert forming["low"] == pytest.approx(330.5)
    assert forming["volume"] == 500
    assert str(forming["ts"])[:10] == "2026-09-17"
    # History intact behind it.
    assert out["bars"][-2]["close"] == pytest.approx(331.50)


def test_chart_forming_bar_declines_without_session_open():
    quote = _quote(as_of="2026-09-17T13:30:00+00:00")
    quote["open"] = None
    svc = _service(quote=quote)
    out = svc.get_chart("AAPL", "1d", 30)
    assert out["stitched"] is False
    assert out["stitched_reason"] == "forming-open-unknown"
    assert out["forming"] is False
    assert len(out["bars"]) == 2


def test_chart_forming_bar_blocked_off_session(monkeypatch):
    import backend.instruments.calendars as calendars_module

    monkeypatch.setattr(calendars_module, "_lib_is_session", lambda day, mic: False)
    svc = _service(quote=_quote(as_of="2026-09-17T13:30:00+00:00"))
    out = svc.get_chart("AAPL", "1d", 30)
    assert out["stitched"] is False
    assert out["stitched_reason"] == "quote-off-session"
    assert len(out["bars"]) == 2


def test_chart_stale_quote_leaves_bars_alone():
    svc = _service(quote=_quote(as_of="2026-09-14T19:00:00+00:00"))
    out = svc.get_chart("AAPL", "1d", 30)
    assert out["stitched"] is False
    assert out["stitched_reason"] == "quote-stale"
    assert out["bars"][-1]["close"] == pytest.approx(331.50)


def test_chart_quote_failure_degrades_bars_survive():
    svc = _service(quote_error=ProviderError("alpaca", "down"))
    out = svc.get_chart("AAPL", "1d", 30)
    assert out["quote"] is None
    assert out["stitched"] is False
    assert out["stitched_reason"] == "quote-missing"
    assert len(out["bars"]) == 2


def test_chart_bars_failure_raises_like_bars():
    svc = _service(bars_error=ProviderError("yfinance", "no live bars"))
    with pytest.raises(ProviderError):
        svc.get_chart("AAPL", "1d", 30)


def test_chart_non_1d_skips_stitch():
    svc = _service()
    out = svc.get_chart("AAPL", "1wk", 30)
    assert out["stitched"] is False
    assert out["stitched_reason"] == "non-1d-timeframe"
    assert out["quote"] is None
    assert out["bars"][-1]["close"] == pytest.approx(331.50)


def test_chart_never_mutates_input_bars():
    payload = _bars()
    snapshot = copy.deepcopy(payload)
    svc = _service(bars=payload)
    svc.get_chart("AAPL", "1d", 30)
    assert payload == snapshot  # shared/cache objects stay pristine
    again = svc.get_chart("AAPL", "1d", 30)
    assert again["bars"][-1]["close"] == pytest.approx(333.28)


# --- endpoint ---------------------------------------------------------------

def test_chart_endpoint_shape_and_sync():
    try:
        resp = _client(_service()).get("/api/market_data/chart",
                                       params={"symbol": "AAPL", "limit": 30})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["symbol"] == "AAPL"
        assert body["stitched"] is True
        assert body["quote"]["price"] == pytest.approx(333.28)
        assert body["bars"][-1]["close"] == pytest.approx(body["quote"]["price"])
        assert PROVENANCE_KEYS <= set(body["provenance"])
    finally:
        _teardown()


def test_chart_endpoint_forming_bar_sync():
    try:
        svc = _service(quote=_quote(as_of="2026-09-17T13:30:00+00:00"))
        resp = _client(svc).get("/api/market_data/chart",
                                params={"symbol": "AAPL", "limit": 30})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["stitched"] is True
        assert body["forming"] is True
        assert body["bars"][-1]["close"] == pytest.approx(body["quote"]["price"])
    finally:
        _teardown()


def test_chart_endpoint_quote_fail_still_200():
    try:
        svc = _service(quote_error=ProviderError("alpaca", "down"))
        resp = _client(svc).get("/api/market_data/chart",
                                params={"symbol": "AAPL"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["quote"] is None
        assert body["stitched"] is False
        assert len(body["bars"]) == 2
    finally:
        _teardown()


def test_chart_endpoint_bars_fail_502_bad_input_422():
    try:
        svc = _service(bars_error=ProviderError("yfinance", "no live bars"))
        client = _client(svc)
        assert client.get("/api/market_data/chart",
                          params={"symbol": "AAPL"}).status_code == 502
        assert client.get("/api/market_data/chart",
                          params={"symbol": "___"}).status_code == 422
        assert client.get("/api/market_data/chart",
                          params={"symbol": "AAPL",
                                  "timeframe": "5m"}).status_code == 422
    finally:
        _teardown()
