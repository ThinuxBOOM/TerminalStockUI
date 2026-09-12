"""M6 SSE tests: resolution both-forms, market filter, name search, calendar, currency."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from backend.api.deps import reset_deps
from backend.api.main import create_app
from backend.instruments.calendars import is_holiday, is_trading_day, market_state_at
from backend.instruments.registry import InstrumentRegistry
from backend.instruments.search import search_instruments
from backend.market_data.health import market_state

SH = ZoneInfo("Asia/Shanghai")


def _registry() -> InstrumentRegistry:
    return InstrumentRegistry()


# -- seed normalization ----------------------------------------------------

def test_sse_exchange_symbol_normalized():
    reg = _registry()
    inst = reg.get_by_mic_symbol("XSHG", "600519")
    assert inst is not None
    assert inst.exchange_symbol == "600519"  # no suffix in exchange_symbol
    assert inst.provider_symbol == "600519.SS"
    assert inst.currency == "CNY"
    assert inst.country == "CN"
    assert inst.timezone == "Asia/Shanghai"
    assert inst.exchange_mic == "XSHG"
    assert inst.isin == "CNE0000018R8"


def test_sse_blue_chip_seeds():
    reg = _registry()
    for sym, prov, isin in (
        ("600036", "600036.SS", "CNE000001B33"),  # CMB
        ("601318", "601318.SS", "CNE000001R84"),  # Ping An
        ("600900", "600900.SS", "CNE000001G38"),  # Yangtze Power
    ):
        inst = reg.get_by_mic_symbol("XSHG", sym)
        assert inst is not None, f"missing SSE seed {sym}"
        assert inst.provider_symbol == prov
        assert inst.currency == "CNY"
        assert inst.country == "CN"
        assert inst.timezone == "Asia/Shanghai"
        assert inst.isin == isin


# -- resolution both forms -------------------------------------------------

def test_resolve_suffix_and_bare_forms():
    reg = _registry()
    inst_suf, _c, amb_suf = reg.resolve("600519.SS")
    assert inst_suf is not None and inst_suf.exchange_mic == "XSHG"
    assert inst_suf.exchange_symbol == "600519"
    assert not amb_suf
    inst_bare, _c2, amb_bare = reg.resolve("600519", market="XSHG")
    assert inst_bare is not None and inst_bare.exchange_mic == "XSHG"
    assert not amb_bare
    assert inst_bare.instrument_id == inst_suf.instrument_id
    # Bare globally resolves to the unique XSHG instrument (no ambiguity).
    inst_g, _c3, amb_g = reg.resolve("600519")
    assert inst_g is not None and inst_g.exchange_mic == "XSHG"
    assert not amb_g


def test_resolve_new_blue_chips_both_forms():
    reg = _registry()
    for bare, suffixed in (("600036", "600036.SS"), ("601318", "601318.SS"), ("600900", "600900.SS")):
        a, _, _ = reg.resolve(suffixed)
        b, _, _ = reg.resolve(bare, market="XSHG")
        assert a is not None and b is not None
        assert a.instrument_id == b.instrument_id


def test_resolve_wrong_market_returns_none():
    reg = _registry()
    inst, _c, _a = reg.resolve("600519", market="XNAS")
    assert inst is None


def test_market_filter_search():
    reg = _registry()
    results = search_instruments(reg.all(), "600", market="XSHG")
    assert results and all(r.exchange_mic == "XSHG" for r in results)
    assert any(r.exchange_symbol == "600519" for r in results)


# -- name search -----------------------------------------------------------

def test_moutai_name_search():
    reg = _registry()
    results = search_instruments(reg.all(), "Moutai")
    assert results, "expected Moutai match"
    assert any(r.exchange_symbol == "600519" for r in results)
    results_lower = search_instruments(reg.all(), "moutai")
    assert any(r.exchange_symbol == "600519" for r in results_lower)


def test_search_result_exposes_required_fields():
    reg = _registry()
    results = search_instruments(reg.all(), "600519")
    assert results
    top = results[0].model_dump_canonical()
    for field in ("company_name", "exchange_mic", "currency", "exchange_symbol", "provider_symbol"):
        assert field in top and top[field], f"missing {field}"
    assert top["company_name"] and "Moutai" in top["company_name"]


# -- calendar --------------------------------------------------------------

def test_xshg_trading_day_weekend_and_holiday_stub():
    assert not is_trading_day(date(2026, 9, 12), "XSHG")  # Saturday
    assert is_trading_day(date(2026, 9, 8), "XSHG")  # Tuesday
    assert is_holiday(date(2026, 1, 1), "XSHG")  # New Year stub
    assert not is_trading_day(date(2026, 1, 1), "XSHG")
    assert not is_trading_day(date(2026, 10, 1), "XSHG")  # National Day stub


def test_xshg_calendar_open_lunch_closed():
    # Tuesday 2026-09-08, a trading day outside the holiday stub.
    assert market_state_at("XSHG", datetime(2026, 9, 8, 10, 0, tzinfo=SH)) == "open"
    assert market_state_at("XSHG", datetime(2026, 9, 8, 12, 0, tzinfo=SH)) == "lunch"
    assert market_state_at("XSHG", datetime(2026, 9, 8, 14, 0, tzinfo=SH)) == "open"
    assert market_state_at("XSHG", datetime(2026, 9, 8, 16, 0, tzinfo=SH)) == "closed"
    assert market_state_at("XSHG", datetime(2026, 9, 8, 8, 0, tzinfo=SH)) == "closed"


def test_xshg_calendar_weekend_closed():
    assert market_state_at("XSHG", datetime(2026, 9, 12, 10, 0, tzinfo=SH)) == "closed"


def test_calendar_freshness_delayed_stale():
    noon = datetime(2026, 9, 8, 10, 0, tzinfo=SH)
    assert market_state_at("XSHG", noon, as_of=noon) == "open"
    assert market_state_at("XSHG", noon, as_of=noon - timedelta(minutes=30)) == "delayed"
    assert market_state_at("XSHG", noon, as_of=noon - timedelta(days=3)) == "stale"


# -- health helper ---------------------------------------------------------

def test_health_market_state_calendar_aware():
    open_at = datetime(2026, 9, 8, 10, 0, tzinfo=SH)
    lunch_at = datetime(2026, 9, 8, 12, 0, tzinfo=SH)
    assert market_state(open_at, delay_minutes=15, now=open_at, mic="XSHG", at=open_at) == "open"
    # Lunch maps to closed in the open|closed|delayed|stale health contract.
    assert market_state(lunch_at, delay_minutes=15, now=lunch_at, mic="XSHG", at=lunch_at) == "closed"
    weekend = datetime(2026, 9, 12, 10, 0, tzinfo=SH)
    assert market_state(weekend, delay_minutes=15, now=weekend, mic="XSHG", at=weekend) == "closed"
    # Stale wins over calendar.
    assert market_state(open_at - timedelta(days=3), delay_minutes=15, now=open_at, mic="XSHG", at=open_at) == "stale"
    # Legacy freshness-only path preserved (no mic).
    now = datetime.now(timezone.utc)
    assert market_state(now, delay_minutes=15, now=now) == "open"
    assert market_state(now - timedelta(minutes=30), delay_minutes=15, now=now) == "delayed"


# -- currency + US unaffected ----------------------------------------------

def test_sse_currency_cny():
    reg = _registry()
    for i in reg.all():
        if i.exchange_mic == "XSHG":
            assert i.currency == "CNY"


def test_us_tickers_unaffected():
    reg = _registry()
    inst, _, _ = reg.resolve("AAPL")
    assert inst is not None and inst.exchange_mic == "XNAS" and inst.exchange_symbol == "AAPL"
    results = search_instruments(reg.all(), "AAP")
    assert results[0].exchange_symbol == "AAP"  # exact NYSE first, not AAPL


# -- HTTP surfaces ---------------------------------------------------------

def test_http_resolve_surfaces_market_state_currency_exchange():
    reset_deps()
    client = TestClient(create_app())
    body = client.get("/api/instruments/resolve", params={"symbol": "600519.SS"}).json()
    assert body["instrument"]["exchange_mic"] == "XSHG"
    assert body["instrument"]["currency"] == "CNY"
    assert body["market_state"] in ("open", "closed", "lunch", "delayed", "stale")
    assert body["currency"] == "CNY"
    assert body["exchange_mic"] == "XSHG"


def test_http_quote_sse_currency_and_state():
    reset_deps()
    client = TestClient(create_app())
    resp = client.get("/api/market_data/quote", params={"symbol": "600519.SS"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["currency"] == "CNY"
    assert (body.get("instrument") or {}).get("exchange_mic") == "XSHG"
    assert body["market_state"] in ("open", "closed", "delayed", "stale")
