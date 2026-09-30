"""Daily bars are stored as the session's local midnight in UTC, so their UTC
date is a day early for venues east of UTC. These tests pin the fixes that
read the exchange-local session day instead (freshness gate, quote stitching,
bar payloads)."""

from __future__ import annotations

from datetime import date, datetime, timezone

import backend.instruments.calendars as calendars
from backend.instruments.calendars import session_date
from backend.market_data.service import MarketDataService


def test_session_date_uses_exchange_local_day():
    assert session_date("2026-09-28T16:00:00+00:00", "XSHG") == date(2026, 9, 29)
    assert session_date("2026-09-29T22:00:00+00:00", "XPAR") == date(2026, 9, 30)
    assert session_date("2026-09-30T04:00:00+00:00", "XNAS") == date(2026, 9, 30)
    assert session_date(datetime(2026, 9, 28, 16, tzinfo=timezone.utc), "XSHG") == date(2026, 9, 29)
    assert session_date("2026-09-30", "XSHG") == date(2026, 9, 30)
    assert session_date("not a date", "XSHG") is None


def _payload(ts: str) -> dict:
    return {"bars": [{"ts": ts, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]}


def test_freshness_gate_reads_shanghai_bars_on_their_session_day(monkeypatch):
    """The 29 Sep Moutai bar (stored 2026-09-28T16:00Z) is one session behind
    30 Sep, which the vendor-settle tolerance accepts. Reading the UTC date
    (28 Sep) called it two sessions stale and failed every SSE chart."""
    monkeypatch.setattr(calendars, "last_completed_trading_day", lambda mic, now=None: date(2026, 9, 30))
    svc = MarketDataService()
    assert svc._bars_payload_is_fresh(_payload("2026-09-28T16:00:00+00:00"), "600519.SS", "1d") is True
    # Genuinely stale (26 Sep session, several trading days back) still fails.
    assert svc._bars_payload_is_fresh(_payload("2026-09-24T16:00:00+00:00"), "600519.SS", "1d") is False


def test_weekend_fetch_updates_fridays_bar_instead_of_growing_a_candle():
    svc = MarketDataService()
    bars = [{"ts": "2026-09-24T04:00:00+00:00", "date": "2026-09-24", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 5},
            {"ts": "2026-09-25T04:00:00+00:00", "date": "2026-09-25", "open": 10, "high": 12, "low": 9, "close": 11, "volume": 6}]
    quote = {"price": 11.5, "open": 10.0, "high": 12.0, "low": 9.0, "volume": 7,
             "price_time": "2026-09-25T20:00:00+00:00",          # Friday close (New York)
             "provenance": {"as_of": "2026-09-26T15:00:00+00:00"}}  # fetched Saturday
    rows, stitched, reason, forming = svc._stitch_quote_into_bars(bars, quote, "XNAS")
    assert stitched is True and reason is None and forming is False
    assert len(rows) == 2
    assert rows[-1]["close"] == 11.5


def test_bars_payload_rows_carry_session_date():
    class _Row:
        def __init__(self, ts):
            self.ts, self.as_of, self.source = ts, ts, "yfinance"
            self.open = self.high = self.low = self.close = 1.0
            self.volume = 1

    class _Inst:
        exchange_mic = "XSHG"
        instrument_id = "XSHG-600519"

    payload = MarketDataService._bars_response_from_rows(
        [_Row(datetime(2026, 9, 28, 16, tzinfo=timezone.utc))],
        db_inst=_Inst(), response_symbol="600519.SS", response_inst_id="XSHG-600519", timeframe="1d",
    )
    assert payload["bars"][0]["ts"].startswith("2026-09-28T16:00")
    assert payload["bars"][0]["date"] == "2026-09-29"


def test_bars_api_exposes_session_date():
    """The response model must not drop ``date`` (it silently did at first)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.deps import get_market_service
    from backend.api.market_data import router
    from backend.tests.auth_helpers import inject_admin_auth

    class _Svc:
        def get_bars(self, symbol, timeframe="1d", limit=30):
            return {
                "symbol": symbol, "instrument_id": "XSHG-600519", "timeframe": "1d",
                "bars": [{"ts": "2026-09-28T16:00:00+00:00", "date": "2026-09-29",
                          "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1}],
                "provenance": {"source": "yfinance", "as_of": "2026-09-30T08:00:00+00:00",
                               "delay_minutes": 15, "quality_grade": "B",
                               "fallback_used": False, "missing_fields": []},
            }

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_market_service] = lambda: _Svc()
    resp = TestClient(inject_admin_auth(app)).get("/api/market_data/bars", params={"symbol": "600519.SS"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["bars"][0]["date"] == "2026-09-29"
